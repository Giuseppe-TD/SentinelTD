from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request
from fastapi.responses import FileResponse, RedirectResponse
import os
import httpx
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Site
from ..schemas import SiteIn, SiteUpdate, SiteOut, SiteDetailOut, BulkTagsIn
from ..auth import require_auth
from ..config import settings
from ..connectors import apply_status, wp_rest_url
from ..i18n import normalize_language, t

router = APIRouter(prefix="/api/sites", tags=["sites"], dependencies=[Depends(require_auth)])

SHOTS_DIR = "/data/screenshots"


async def _enqueue(job: str, *args):
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await pool.enqueue_job(job, *args)
    finally:
        await pool.aclose()


def _tag_list(value) -> list[str]:
    """
    Spezza una stringa CSV (o una lista) di tag in lista pulita:
    - strip degli spazi, niente vuoti
    - dedup case-insensitive preservando la PRIMA occorrenza (mantiene il case scelto)
    """
    if isinstance(value, (list, tuple)):
        raw = []
        for v in value:
            raw.extend(str(v).split(","))
    else:
        raw = str(value or "").split(",")
    out, seen = [], set()
    for t in raw:
        t = " ".join(t.split()).strip()   # collassa spazi multipli interni
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _norm_tags(value) -> str:
    """Ritorna la forma canonica CSV ('tagA,tagB') da salvare in colonna."""
    return ",".join(_tag_list(value))


@router.get("", response_model=list[SiteOut])
async def list_sites(s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Site).order_by(Site.group, Site.name))).scalars().all()
    return rows


@router.post("/bulk/auto-update")
async def bulk_auto_update(enabled: bool = Query(...), s: AsyncSession = Depends(get_session)):
    """Attiva/disattiva l'auto-update su TUTTI i siti in un colpo solo.
    Utile dopo un inserimento batch (dove i siti partono con auto_update=false)."""
    await s.execute(sa_update(Site).values(auto_update=enabled))
    await s.commit()
    n = (await s.execute(select(Site))).scalars().all()
    return {"updated": len(n), "auto_update": enabled}


@router.post("/bulk/update-now")
async def bulk_update_now(s: AsyncSession = Depends(get_session)):
    """
    MASS UPDATE on-demand: lancia SUBITO l'aggiornamento di tutti i siti che hanno
    update pending (senza aspettare il ciclo orario). Rispetta il flag auto_update.
    Restituisce quanti siti verranno aggiornati, cosi' la UI puo' confermare.
    Il lavoro vero (accodamento scaglionato + riepilogo Telegram) lo fa il job
    mass_update_now nel worker.
    """
    # conta quanti siti hanno davvero update pending (per il messaggio in UI)
    rows = (await s.execute(
        select(Site).where(Site.enabled == True, Site.auto_update == True)  # noqa: E712
    )).scalars().all()
    pending = [
        site for site in rows
        if site.core_update
        or (site.upd_plugins or 0) > 0
        or (site.upd_themes or 0) > 0
        or (site.upd_other or 0) > 0
    ]
    # accoda il job worker che fa il lavoro
    await _enqueue("mass_update_now")
    return {"queued": len(pending), "sites": [s.name for s in pending]}


@router.post("/bulk/update-selected")
async def bulk_update_selected(
    ids: list[int] = Body(..., embed=True),
    s: AsyncSession = Depends(get_session),
):
    """
    Aggiorna SOLO i siti selezionati (pulsante 'Aggiorna selezionati').
    Riceve la lista di id dalla UI. A differenza di update-now non filtra per pending:
    l'utente li ha scelti esplicitamente. Rispetta enabled + auto_update.
    """
    if not ids:
        return {"queued": 0, "sites": []}
    rows = (await s.execute(
        select(Site).where(
            Site.id.in_(ids),
            Site.enabled == True,       # noqa: E712
            Site.auto_update == True,    # noqa: E712
        )
    )).scalars().all()
    await _enqueue("mass_update_selected", [site.id for site in rows])
    return {"queued": len(rows), "sites": [site.name for site in rows]}


@router.post("/bulk/tags")
async def bulk_tags(payload: BulkTagsIn, s: AsyncSession = Depends(get_session)):
    """
    Aggiunge e/o rimuove tag su un set di siti selezionati in un colpo solo.
    - add: tag aggiunti (idempotente: non duplica)
    - remove: tag tolti (match case-insensitive)
    Operazione fatta in Python sito-per-sito cosi' i tag restano normalizzati e
    senza duplicati. Con poche decine/centinaia di siti e' piu' che veloce.
    """
    if not payload.site_ids:
        raise HTTPException(422, "Nessun sito selezionato")
    add = _tag_list(payload.add)
    remove_lc = {t.lower() for t in _tag_list(payload.remove)}
    if not add and not remove_lc:
        raise HTTPException(422, "Nessun tag da aggiungere o rimuovere")

    rows = (await s.execute(select(Site).where(Site.id.in_(payload.site_ids)))).scalars().all()
    for site in rows:
        current = _tag_list(site.tags)
        # rimuovi (case-insensitive)
        if remove_lc:
            current = [t for t in current if t.lower() not in remove_lc]
        # aggiungi (dedup case-insensitive)
        existing = {t.lower() for t in current}
        for t in add:
            if t.lower() not in existing:
                current.append(t)
                existing.add(t.lower())
        site.tags = ",".join(current)
    await s.commit()
    return {"updated": len(rows), "added": add, "removed": list(remove_lc)}


async def _resolve_final_url(url: str) -> str:
    """
    Segue i redirect del sito UNA volta e ritorna l'URL finale "vero" (con/senza www,
    https), cosi' tutto cio' che costruiamo dopo (admin, status) parte gia' dall'URL
    giusto e non sbatte contro i redirect del sito. Se fallisce, torna l'url originale.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.head(u + "/", headers={"User-Agent": "PanopticonLite"})
            # url finale dopo i redirect (preserva www/https reali del sito)
            final = str(r.url).rstrip("/")
            # tieni solo schema://host (butta eventuale path aggiunto da redirect lingua)
            from urllib.parse import urlparse
            p = urlparse(final)
            if p.scheme and p.netloc:
                return f"{p.scheme}://{p.netloc}"
    except Exception:  # noqa: BLE001
        pass
    return u


@router.post("", response_model=SiteOut, status_code=201)
async def create_site(payload: SiteIn, s: AsyncSession = Depends(get_session)):
    if payload.cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    if not payload.token.strip():
        raise HTTPException(422, "Token mancante: copialo dal connettore installato sul sito")
    # risolvi l'URL reale del sito (segue redirect: www, https) cosi' admin/status partono giusti
    resolved = await _resolve_final_url(payload.url)
    data = payload.model_dump(exclude={"admin_url"})
    data["url"] = resolved
    data["tags"] = _norm_tags(data.get("tags"))
    admin = payload.admin_url or (
        f"{resolved.rstrip('/')}/wp-admin/" if payload.cms == "wp"
        else f"{resolved.rstrip('/')}/administrator/"
    )
    site = Site(**data, admin_url=admin)
    s.add(site)
    await s.commit()
    await s.refresh(site)
    await _enqueue("poll_site", site.id)
    return site


@router.get("/{site_id}", response_model=SiteDetailOut)
async def get_site(site_id: int, s: AsyncSession = Depends(get_session)):
    site = await s.get(Site, site_id)
    if not site:
        raise HTTPException(404)
    return site


@router.patch("/{site_id}", response_model=SiteOut)
async def update_site(site_id: int, payload: SiteUpdate, s: AsyncSession = Depends(get_session)):
    site = await s.get(Site, site_id)
    if not site:
        raise HTTPException(404)
    data = payload.model_dump(exclude_none=True)
    # tags: normalizza in forma canonica CSV se passati esplicitamente
    if "tags" in data:
        data["tags"] = _norm_tags(data["tags"])
    # se viene cambiato l'URL, risolvilo (segue redirect www/https) e riallinea l'admin_url
    if "url" in data and data["url"]:
        old_url = (site.url or "").rstrip("/")
        data["url"] = await _resolve_final_url(data["url"])
        # se cambia dominio/URL, invalida il dato WHOIS/RDAP: il prossimo giro lo
        # ricalcola subito invece di mostrare per una settimana la scadenza precedente.
        if data["url"].rstrip("/") != old_url:
            site.domain_name = ""
            site.domain_expires_at = None
            site.domain_checked_at = None
            site.domain_check_error = ""
            site.domain_alert_state = ""
        # se l'admin_url non e' stato passato esplicitamente, ricostruiscilo dal nuovo url
        if "admin_url" not in data or not (data.get("admin_url") or "").strip():
            base = data["url"].rstrip("/")
            data["admin_url"] = f"{base}/wp-admin/" if site.cms == "wp" else f"{base}/administrator/"
    for k, v in data.items():
        setattr(site, k, v)
    await s.commit()
    await s.refresh(site)
    return site


@router.delete("/{site_id}", status_code=204)
async def delete_site(site_id: int, s: AsyncSession = Depends(get_session)):
    site = await s.get(Site, site_id)
    if site:
        await s.delete(site)
        await s.commit()


@router.post("/{site_id}/refresh", response_model=SiteDetailOut)
async def refresh_now(site_id: int, s: AsyncSession = Depends(get_session)):
    """Check immediato sincrono (per il pulsante 'aggiorna ora' in dashboard)."""
    site = await s.get(Site, site_id)
    if not site:
        raise HTTPException(404)
    # pulsante 'Check' manuale: forza il refresh lato connettore (come l'icona reload
    # di Akeeba). I check automatici schedulati restano passivi (force=False).
    await apply_status(s, site, force=True)
    await s.commit()
    await s.refresh(site)
    return site


@router.post("/{site_id}/screenshot", response_model=SiteOut)
async def screenshot_now(site_id: int, s: AsyncSession = Depends(get_session)):
    """Screenshot on-demand sincrono (attende il shooter)."""
    site = await s.get(Site, site_id)
    if not site:
        raise HTTPException(404)
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            r = await client.post(f"{settings.SHOOTER_URL}/shot", json={"url": site.url, "site_id": site.id})
            r.raise_for_status()
            from datetime import datetime, timezone
            site.shot_path = r.json()["path"]
            site.shot_at = datetime.now(timezone.utc)
            await s.commit()
            await s.refresh(site)
    except httpx.TimeoutException:
        raise HTTPException(504, "Shooter timeout: il sito ci mette troppo a rispondere")
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"Shooter error: {ex}")
    return site


def _build_admin_url(site) -> str:
    """
    Costruisce l'URL admin COMPLETO e gia' corretto, cosi' il browser ci va dritto
    senza dipendere dai redirect del sito (slash mancante, http->https, ecc.).
    - parte sempre da site.url (lo schema/www che hai messo tu)
    - se hai un admin_url custom lo usa, altrimenti /administrator/ o /wp-admin/
    - garantisce lo slash finale (evita il redirect di mod_dir che butta in http)
    """
    base = (site.url or "").strip().rstrip("/")
    custom = (site.admin_url or "").strip()

    if custom:
        # se l'admin_url custom e' un path relativo (/administrator) lo attacco a base
        if custom.startswith("/"):
            url = base + custom
        elif custom.startswith("http://") or custom.startswith("https://"):
            url = custom
        else:
            url = base + "/" + custom
    else:
        url = base + ("/wp-admin/" if site.cms == "wp" else "/administrator/")

    # garantisci lo slash finale SOLO se non c'e' query string o file finale
    # (per /administrator/ e /wp-admin/ vogliamo lo slash; se c'e' ?param o .php lascio com'e')
    if "?" not in url and not url.rsplit("/", 1)[-1].count("."):
        if not url.endswith("/"):
            url += "/"
    return url


@router.get("/{site_id}/admin")
async def open_admin(site_id: int, autologin: bool = Query(False), s: AsyncSession = Depends(get_session)):
    """
    Ritorna l'URL admin gia' completo e normalizzato (slash finale, schema corretto),
    cosi' il browser ci arriva dritto senza passare per i redirect del sito.
    Per WordPress con autologin=true chiede al connettore un link one-time.
    """
    site = await s.get(Site, site_id)
    if not site:
        raise HTTPException(404)

    admin_url = _build_admin_url(site)

    if site.cms == "wp" and autologin:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.post(
                    wp_rest_url(site, "autologin"),
                    headers={"Authorization": f"Bearer {site.token}"},
                )
                r.raise_for_status()
                u = r.json().get("url")
                if u:
                    return {"url": u}
        except Exception:  # noqa: BLE001
            pass  # fallback sotto
    return {"url": admin_url}


@router.post("/telegram/test")
async def telegram_test(request: Request):
    """Invia un messaggio di prova al chat Telegram configurato. Per il pulsante 'Test'
    nella UI: verifica al volo che token/chat_id siano corretti."""
    from ..telegram import send_telegram, _enabled
    language = normalize_language(request.headers.get("X-UI-Language"), "en")
    if not _enabled():
        raise HTTPException(400, t("Telegram non configurato: imposta TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nel .env", language))
    ok = await send_telegram("🔔 <b>Sentinel TD</b> — " + t("messaggio di test. Le notifiche funzionano!", language))
    if not ok:
        raise HTTPException(502, t("Invio fallito: controlla token/chat_id o la connettività", language))
    return {"ok": True}
