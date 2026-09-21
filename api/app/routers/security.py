"""
CENTRO SICUREZZA - Router / endpoint API.

Nuovo file: api/app/routers/security.py

Espone i dati per la pagina Centro Sicurezza:
  GET  /api/security/summary   -> contatori per la campanella + totali
  GET  /api/security/matches   -> elenco vulnerabilita' attive per sito (con filtri)
  POST /api/security/scan-now  -> forza subito una scansione (accoda il job worker)

Tutti protetti da require_auth (come gli altri).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from arq import create_pool
from arq.connections import RedisSettings

from ..db import get_session
from ..models import Site, Extension, Vulnerability, VulnMatch
from ..auth import require_auth
from ..config import settings

router = APIRouter(prefix="/api/security", tags=["security"], dependencies=[Depends(require_auth)])


async def _enqueue(job: str, *args):
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await pool.enqueue_job(job, *args)
    finally:
        await pool.aclose()


@router.get("/summary")
async def security_summary(s: AsyncSession = Depends(get_session)):
    """
    Contatori per la campanella nell'header e per le card in cima alla dashboard.
    'bell' e' True se c'e' almeno una vulnerabilita' GRAVE (sfruttata o critical) attiva.
    """
    q = (
        select(Vulnerability.severity, Vulnerability.exploited_in_wild, func.count(VulnMatch.id))
        .join(VulnMatch, VulnMatch.vulnerability_id == Vulnerability.id)
        .where(VulnMatch.is_vulnerable == True)  # noqa: E712
        .group_by(Vulnerability.severity, Vulnerability.exploited_in_wild)
    )
    rows = (await s.execute(q)).all()

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    exploited = 0
    total = 0
    for severity, is_expl, n in rows:
        counts[severity or "unknown"] = counts.get(severity or "unknown", 0) + n
        total += n
        if is_expl:
            exploited += n

    # quanti siti distinti hanno almeno una vulnerabilita' attiva
    sites_affected = (await s.execute(
        select(func.count(func.distinct(VulnMatch.site_id))).where(VulnMatch.is_vulnerable == True)  # noqa: E712
    )).scalar() or 0

    # ultima scansione: max fetched_at
    last_scan = (await s.execute(select(func.max(Vulnerability.fetched_at)))).scalar()

    bell = exploited > 0 or counts["critical"] > 0
    return {
        "bell": bell,
        "total": total,
        "exploited": exploited,
        "critical": counts["critical"],
        "high": counts["high"],
        "medium": counts["medium"],
        "low": counts["low"],
        "sites_affected": sites_affected,
        "last_scan": last_scan.isoformat() if last_scan else None,
    }


@router.get("/matches")
async def security_matches(
    s: AsyncSession = Depends(get_session),
    severity: str | None = Query(None),   # filtro: critical/high/medium/low
    cms: str | None = Query(None),        # filtro: joomla/wp
    only_exploited: bool = Query(False),  # solo sfruttate in the wild
    site_id: int | None = Query(None),
):
    """
    Elenco delle vulnerabilita' ATTIVE, una riga per match (sito+estensione+vuln),
    ordinato per priorita' (sfruttate, poi cvss/severita' decrescente).
    """
    q = (
        select(VulnMatch, Vulnerability, Site)
        .join(Vulnerability, Vulnerability.id == VulnMatch.vulnerability_id)
        .join(Site, Site.id == VulnMatch.site_id)
        .where(VulnMatch.is_vulnerable == True)  # noqa: E712
    )
    if severity:
        q = q.where(Vulnerability.severity == severity)
    if cms:
        q = q.where(Site.cms == cms)
    if only_exploited:
        q = q.where(Vulnerability.exploited_in_wild == True)  # noqa: E712
    if site_id:
        q = q.where(Site.id == site_id)

    rows = (await s.execute(q)).all()

    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
    items = []
    for m, v, site in rows:
        items.append({
            "match_id": m.id,
            "site_id": site.id,
            "site_name": site.name,
            "site_url": site.url,
            "cms": site.cms,
            "ext_name": "",   # riempito sotto via extension_id
            "ext_slug": "",
            "site_version": m.site_version,
            "cve_id": v.cve_id,
            "title": v.title,
            "severity": v.severity,
            "cvss": v.cvss,
            "exploited": v.exploited_in_wild,
            "version_fixed": v.version_fixed,
            "source": v.source,
            "url": v.url,
            "first_seen": m.first_seen.isoformat() if m.first_seen else None,
        })

    # arricchisci con nome estensione (dalla tabella extensions via extension_id)
    ext_ids = [m.extension_id for m, _, _ in rows if m.extension_id]
    if ext_ids:
        exts = (await s.execute(select(Extension).where(Extension.id.in_(ext_ids)))).scalars().all()
        ext_map = {e.id: e for e in exts}
        for (m, v, site), item in zip(rows, items):
            e = ext_map.get(m.extension_id)
            if e:
                item["ext_name"] = e.name
                item["ext_slug"] = e.slug

    items.sort(key=lambda x: (1 if x["exploited"] else 0, sev_rank.get(x["severity"], 0), x["cvss"]), reverse=True)
    return {"count": len(items), "items": items}


@router.post("/scan-now")
async def security_scan_now():
    """Forza subito una scansione vulnerabilita' (accoda il job worker security_scan)."""
    await _enqueue("security_scan")
    return {"queued": True}
