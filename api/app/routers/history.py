"""
Storico update (7 giorni) per Sentinel: timeline per sito e riepilogo dashboard.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import UpdateHistory
from ..auth import require_auth

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


@router.get("")
async def list_history(
    site_id: int | None = Query(None),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(300, ge=1, le=2000),
    s: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(UpdateHistory).where(UpdateHistory.created_at >= since)
    if site_id:
        q = q.where(UpdateHistory.site_id == site_id)
    q = q.order_by(UpdateHistory.created_at.desc()).limit(limit)
    rows = (await s.execute(q)).scalars().all()
    return [{
        "id": r.id, "site_id": r.site_id, "site_name": r.site_name, "cms": r.cms,
        "type": r.ext_type, "name": r.ext_name, "slug": r.slug,
        "from": r.from_version, "to": r.to_version, "ok": r.ok, "error": r.error,
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.get("/summary")
async def history_summary(days: int = Query(7, ge=1, le=30), s: AsyncSession = Depends(get_session)):
    """Conteggi per giorno (ok/falliti) per il grafico della dashboard + totali."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = func.date_trunc("day", UpdateHistory.created_at)
    rows = (await s.execute(
        select(day.label("d"), UpdateHistory.ok, func.count()).where(UpdateHistory.created_at >= since)
        .group_by(day, UpdateHistory.ok).order_by(day)
    )).all()
    per_day: dict[str, dict] = {}
    for d, ok, n in rows:
        key = d.date().isoformat()
        per_day.setdefault(key, {"day": key, "ok": 0, "failed": 0})
        per_day[key]["ok" if ok else "failed"] += n
    # riempi i giorni vuoti
    out = []
    for i in range(days - 1, -1, -1):
        k = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
        out.append(per_day.get(k, {"day": k, "ok": 0, "failed": 0}))
    tot_ok = sum(x["ok"] for x in out)
    tot_failed = sum(x["failed"] for x in out)
    return {"days": out, "ok": tot_ok, "failed": tot_failed}
