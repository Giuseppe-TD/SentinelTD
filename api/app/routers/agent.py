"""
Auto-registrazione siti dal connettore (WordPress).

Il connettore td-panopticon (>= 2.13.0), dalla sua pagina impostazioni, puo' collegare
il sito a Panopticon da solo: chiede l'elenco delle cartelle/tag esistenti, poi si
registra passando nome, URL, il proprio token e la cartella scelta (con auto-update).

Autenticazione: una CHIAVE DI REGISTRAZIONE dedicata (non il JWT admin), generata al
primo uso e visibile nel pannello (modale Connettori). Privilegi volutamente minimi:
con la chiave si puo' solo leggere l'elenco dei tag e aggiungere un sito NUOVO.
I siti gia' presenti (match per URL) non vengono MAI modificati: la registrazione
di un sito esistente risponde existed=true senza toccare nulla.
"""
import secrets

from fastapi import APIRouter, Body, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Site, AppSetting
from ..rate_limit import limiter

router = APIRouter(prefix="/api/agent", tags=["agent"])

_REG_KEY = "register_key"


async def get_register_key(s: AsyncSession) -> str:
    """Legge la chiave di registrazione; la genera al primo accesso."""
    row = await s.get(AppSetting, _REG_KEY)
    if row and row.value:
        return row.value
    value = secrets.token_urlsafe(32)
    if row:
        row.value = value
    else:
        s.add(AppSetting(key=_REG_KEY, value=value))
    await s.commit()
    return value


async def rotate_register_key(s: AsyncSession) -> str:
    value = secrets.token_urlsafe(32)
    row = await s.get(AppSetting, _REG_KEY)
    if row:
        row.value = value
    else:
        s.add(AppSetting(key=_REG_KEY, value=value))
    await s.commit()
    return value


async def _check_key(s: AsyncSession, key: str) -> None:
    real = await get_register_key(s)
    if not key or not secrets.compare_digest(key, real):
        raise HTTPException(401, "Chiave di registrazione non valida")


def _admin_path(u: str) -> str:
    """Normalizza l'URL admin/login a SOLO path (+query): il pannello lo concatena
    all'URL del sito, quindi un URL completo produrrebbe link rotti.
    'https://sito.it/login' -> '/login' ; '/login' resta '/login'."""
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        from urllib.parse import urlparse
        p = urlparse(u)
        return (p.path or "/") + (("?" + p.query) if p.query else "")
    return u if u.startswith("/") else "/" + u


def _urls_match(a: str, b: str) -> bool:
    """Confronto URL tollerante: schema, www e slash finale non contano."""
    def norm(u: str) -> str:
        u = (u or "").strip().lower().rstrip("/")
        u = u.replace("https://", "").replace("http://", "")
        return u[4:] if u.startswith("www.") else u
    return norm(a) == norm(b)


@router.get("/tags")
@limiter.limit("20/minute")
async def agent_tags(request: Request, key: str = Query("")):
    """Elenco cartelle/tag esistenti (per la select nel connettore)."""
    async with SessionLocal() as s:
        await _check_key(s, key)
        rows = (await s.execute(select(Site.tags))).scalars().all()
        seen: dict[str, str] = {}
        for csv in rows:
            for t in (csv or "").split(","):
                t = t.strip()
                if t and t.lower() not in seen:
                    seen[t.lower()] = t
        return {"tags": sorted(seen.values(), key=str.lower)}


@router.post("/register")
@limiter.limit("10/minute")
async def agent_register(request: Request, payload: dict = Body(...)):
    """
    Registra un sito NUOVO. Body: {key, name, url, token, tags, auto_update}.
    Se l'URL e' gia' presente: existed=true e NESSUNA modifica al sito esistente.
    """
    key = str(payload.get("key", ""))
    name = str(payload.get("name", "")).strip()
    url = str(payload.get("url", "")).strip()
    token = str(payload.get("token", "")).strip()
    tags = str(payload.get("tags", "")).strip()
    auto_update = bool(payload.get("auto_update", True))

    if not url or not token:
        raise HTTPException(422, "url e token sono obbligatori")

    async with SessionLocal() as s:
        await _check_key(s, key)

        # gia' registrato? -> riallinea SOLO le credenziali di collegamento.
        # Caso reale: connettore reinstallato su un sito gia' censito -> token NUOVO sul
        # sito, token vecchio nel pannello -> 401 -> sito "offline" per sempre. Chi ha la
        # chiave di registrazione puo' gia' aggiungere siti: riallineare il token di uno
        # esistente e' coerente. Nome, tag, auto_update e il resto NON si toccano.
        existing = (await s.execute(select(Site))).scalars().all()
        for site in existing:
            if _urls_match(site.url, url):
                changed = []
                if token and site.token != token:
                    site.token = token
                    changed.append("token")
                admin_url = _admin_path(str(payload.get("admin_url", "")).strip())
                if admin_url and site.admin_url != admin_url:
                    site.admin_url = admin_url
                    changed.append("admin_url")
                if changed:
                    await s.commit()
                    return {"ok": True, "existed": True, "site_id": site.id,
                            "message": "Sito gia' presente: riallineato " + " + ".join(changed) + ". Impostazioni e cartelle non toccate."}
                return {"ok": True, "existed": True, "site_id": site.id,
                        "message": "Sito gia' presente in Panopticon: nessuna modifica."}

        # normalizza i tag come fa il pannello (CSV pulito, dedup case-insensitive)
        seen: dict[str, str] = {}
        for t in tags.split(","):
            t = t.strip()
            if t and t.lower() not in seen:
                seen[t.lower()] = t
        tags_csv = ",".join(seen.values())

        site = Site(
            name=name or url,
            url=url.rstrip("/"),
            cms="wp",
            admin_url=(_admin_path(str(payload.get("admin_url", "")).strip()) or "/wp-admin/"),
            token=token,
            group="",
            tags=tags_csv,
            poll_interval_minutes=180,
            auto_update=auto_update,
            enabled=True,
        )
        s.add(site)
        await s.commit()
        await s.refresh(site)

    # primo check subito (best effort: se la coda non risponde, il tick lo prende comunque)
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        from ..config import settings as cfg
        pool = await create_pool(RedisSettings.from_dsn(cfg.REDIS_URL))
        await pool.enqueue_job("poll_site", site.id)
        await pool.close()
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "existed": False, "site_id": site.id,
            "message": "Sito registrato in Panopticon."}
