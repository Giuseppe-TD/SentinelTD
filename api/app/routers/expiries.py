"""Scadenze domini (automatiche) e registro globale plugin/temi/licenze."""
from datetime import datetime, timezone
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import settings
from ..db import get_session
from ..models import Site, SiteExpiry
from ..schemas import SiteExpiryIn, SiteExpiryOut, SiteExpiryUpdate

router = APIRouter(prefix="/api", tags=["expiries"], dependencies=[Depends(require_auth)])
_ALLOWED_PLATFORMS = {"wp", "joomla", "both"}
_MULTI_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "net.nz", "co.jp", "ne.jp", "or.jp", "com.br", "com.mx",
    "com.tr", "com.cn", "net.cn", "org.cn", "com.sg", "com.hk", "co.za", "edu.it", "gov.it",
}


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _days(dt: datetime | None) -> int | None:
    if not dt:
        return None
    return (_utc(dt).date() - datetime.now(timezone.utc).date()).days


async def _enqueue(job: str, *args):
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await pool.enqueue_job(job, *args)
    finally:
        await pool.aclose()


def _registrable_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").strip(".").lower()
    labels = [x for x in host.split(".") if x]
    if len(labels) < 2:
        return host
    suffix2 = ".".join(labels[-2:])
    if suffix2 in _MULTI_LEVEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _platform(value: str | None) -> str:
    v = (value or "both").strip().lower()
    return v if v in _ALLOWED_PLATFORMS else "both"


def _recur_label(n: int) -> str:
    return {0: "una tantum", 1: "mensile", 3: "trimestrale", 6: "semestrale", 12: "annuale", 24: "biennale"}.get(n, f"ogni {n} mesi" if n > 0 else "una tantum")


def _clamp_recur(v) -> int:
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, min(120, n))


def _add_months(dt: datetime, months: int) -> datetime:
    """Sposta avanti di N mesi mantenendo il giorno (o l'ultimo del mese se non esiste)."""
    import calendar
    y, mo = dt.year, dt.month + months
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    day = min(dt.day, calendar.monthrange(y, mo)[1])
    return dt.replace(year=y, month=mo, day=day)


def _component(row: SiteExpiry) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category or "Licenza",
        "platform": _platform(row.platform),
        "provider": row.provider,
        "notes": row.notes,
        "expires_at": row.expires_at.isoformat(),
        "days": _days(row.expires_at),
        "recur_months": int(row.recur_months or 0),
        "recur_label": _recur_label(int(row.recur_months or 0)),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------------------------------------------------------------------------
# SCADENZE DOMINI — automatiche, deduplicate per dominio registrabile
# ---------------------------------------------------------------------------
@router.get("/domain-expiries")
async def list_domain_expiries(s: AsyncSession = Depends(get_session)):
    sites = (await s.execute(select(Site))).scalars().all()
    grouped: dict[str, list[Site]] = {}
    for site in sites:
        domain = (_registrable_from_url(site.domain_name or site.url) or site.url).lower().strip(".")
        grouped.setdefault(domain, []).append(site)

    domains: list[dict] = []
    for domain, members in grouped.items():
        with_expiry = next((x for x in members if x.domain_expires_at), None)
        expiry = with_expiry.domain_expires_at if with_expiry else None
        checked = max((x.domain_checked_at for x in members if x.domain_checked_at), default=None)
        errors = [x.domain_check_error for x in members if x.domain_check_error]
        domains.append({
            "id": f"domain:{domain}",
            "name": domain,
            "site_names": [x.name for x in members],
            "site_ids": [x.id for x in members],
            "site_count": len(members),
            "expires_at": expiry.isoformat() if expiry else None,
            "days": _days(expiry),
            "checked_at": checked.isoformat() if checked else None,
            "error": errors[0] if errors else "",
        })
    domains.sort(key=lambda x: (x["days"] is None, x["days"] if x["days"] is not None else 10**9, x["name"]))
    return domains


@router.post("/domain-expiries/scan")
async def scan_domains_now():
    await _enqueue("domain_expiry_scan", True, None)
    return {"queued": True}


# ---------------------------------------------------------------------------
# PLUGIN / TEMI / LICENZE — registro GLOBALE, non associato ai singoli siti
# ---------------------------------------------------------------------------
@router.get("/component-expiries")
async def list_component_expiries(s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(SiteExpiry).order_by(SiteExpiry.expires_at, SiteExpiry.name))).scalars().all()
    return [_component(row) for row in rows]


@router.post("/component-expiries", status_code=201)
async def create_component_expiry(payload: SiteExpiryIn, s: AsyncSession = Depends(get_session)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Nome plugin/tema/licenza obbligatorio")
    row = SiteExpiry(
        site_id=None,
        name=name,
        category=(payload.category or "Licenza").strip() or "Licenza",
        platform=_platform(payload.platform),
        provider=payload.provider.strip(),
        notes=payload.notes.strip(),
        expires_at=_utc(payload.expires_at),
        recur_months=_clamp_recur(payload.recur_months),
        alert_state="",
    )
    s.add(row)
    await s.commit()
    await s.refresh(row)
    return _component(row)


@router.patch("/component-expiries/{expiry_id}")
async def update_component_expiry(expiry_id: int, payload: SiteExpiryUpdate, s: AsyncSession = Depends(get_session)):
    row = await s.get(SiteExpiry, expiry_id)
    if not row:
        raise HTTPException(404, "Scadenza non trovata")
    data = payload.model_dump(exclude_none=True)
    if "name" in data:
        data["name"] = data["name"].strip()
        if not data["name"]:
            raise HTTPException(422, "Nome plugin/tema/licenza obbligatorio")
    if "platform" in data:
        data["platform"] = _platform(data["platform"])
    for k in ("provider", "notes", "category"):
        if k in data:
            data[k] = (data[k] or "").strip()
    if "expires_at" in data:
        data["expires_at"] = _utc(data["expires_at"])
        if data["expires_at"].date() != row.expires_at.date():
            row.alert_state = ""
    if "recur_months" in data:
        data["recur_months"] = _clamp_recur(data["recur_months"])
    for k, v in data.items():
        setattr(row, k, v)
    row.site_id = None
    row.updated_at = datetime.now(timezone.utc)
    await s.commit()
    await s.refresh(row)
    return _component(row)


@router.post("/component-expiries/{expiry_id}/renew")
async def renew_component_expiry(expiry_id: int, s: AsyncSession = Depends(get_session)):
    """Segna la licenza come rinnovata: la scadenza avanza di un periodo (dalla data di
    scadenza, come fanno i vendor - non da oggi). Se era scaduta da piu' periodi, avanza
    finche' non torna nel futuro. Azzera gli avvisi cosi' ripartono per il nuovo ciclo."""
    row = await s.get(SiteExpiry, expiry_id)
    if not row:
        raise HTTPException(404, "Scadenza non trovata")
    n = int(row.recur_months or 0)
    if n <= 0:
        raise HTTPException(400, "Questa scadenza e' una tantum: modifica la data a mano")
    now = datetime.now(timezone.utc)
    nxt = _add_months(row.expires_at, n)
    guard = 0
    while nxt <= now and guard < 240:
        nxt = _add_months(nxt, n)
        guard += 1
    row.expires_at = nxt
    row.alert_state = ""
    row.updated_at = now
    await s.commit()
    await s.refresh(row)
    return _component(row)


@router.delete("/component-expiries/{expiry_id}", status_code=204)
async def delete_component_expiry(expiry_id: int, s: AsyncSession = Depends(get_session)):
    row = await s.get(SiteExpiry, expiry_id)
    if row:
        await s.delete(row)
        await s.commit()


# Compatibilita' temporanea con client precedenti: restituisce entrambe le sezioni.
@router.get("/expiries")
async def legacy_expiries(s: AsyncSession = Depends(get_session)):
    domains = await list_domain_expiries(s)
    components = await list_component_expiries(s)
    return {"domains": domains, "components": components, "items": domains + components}
