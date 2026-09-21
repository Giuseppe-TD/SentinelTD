import os
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Site
from ..auth import verify_token, require_auth

# Nessuna dependency globale: l'immagine si autentica col token in query string,
# perche' <img>/background-image non possono inviare l'header Authorization.
router = APIRouter(prefix="/api/sites", tags=["media"])

SHOTS_DIR = "/data/screenshots"


@router.get("/{site_id}/image")
async def get_image(site_id: int, k: str = Query(""), thumb: int = Query(0),
                    s: AsyncSession = Depends(get_session)):
    if not verify_token(k):
        raise HTTPException(401, "Invalid token")
    site = await s.get(Site, site_id)
    if not site or not site.shot_path:
        raise HTTPException(404)
    # difesa in profondita': shot_path arriva dallo shooter (interno, restituisce
    # sempre "site_{id}.jpg"), ma non mi fido ciecamente. Risolvo il path reale e
    # verifico che resti DENTRO SHOTS_DIR: blocca eventuali "../" o path assoluti.
    base = os.path.realpath(SHOTS_DIR)
    rel = site.shot_path
    if thumb:
        # miniatura generata dallo shooter; se manca (screenshot vecchio) si ripiega
        # sull'immagine intera, cosi' la lista funziona lo stesso
        cand = rel[:-4] + "_thumb.jpg" if rel.lower().endswith(".jpg") else rel + "_thumb.jpg"
        if os.path.isfile(os.path.realpath(os.path.join(SHOTS_DIR, cand))):
            rel = cand
    full = os.path.realpath(os.path.join(SHOTS_DIR, rel))
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(404)
    if not os.path.isfile(full):
        raise HTTPException(404)
    return FileResponse(full, media_type="image/jpeg")


@router.post("/{site_id}/shot", dependencies=[Depends(require_auth)])
async def shoot_now(site_id: int, s: AsyncSession = Depends(get_session)):
    """Rigenera subito l'anteprima del sito (accoda il job allo shooter)."""
    site = await s.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Sito non trovato")
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        from ..config import settings
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        await pool.enqueue_job("shoot_site", site.id)
        await pool.close()
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(503, f"Coda non raggiungibile: {ex}")
    return {"ok": True, "queued": True}


@router.post("/bulk/shots", dependencies=[Depends(require_auth)])
async def shoot_bulk(payload: dict = Body(default={}), s: AsyncSession = Depends(get_session)):
    """Rigenera le anteprime in blocco: tutti i siti abilitati o solo gli id passati.

    Gli scatti vengono scaglionati (uno ogni 12s): lo shooter cattura una pagina per
    volta e ogni scatto puo' durare parecchi secondi, quindi accodarli tutti insieme
    terrebbe occupati i worker e farebbe scadere le chiamate in attesa del lock.
    """
    ids = payload.get("ids") or []
    rows = (await s.execute(select(Site).where(Site.enabled == True))).scalars().all()  # noqa: E712
    if ids:
        wanted = {int(x) for x in ids}
        rows = [x for x in rows if x.id in wanted]
    if not rows:
        return {"queued": 0, "eta_min": 0}

    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        from ..config import settings
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        for i, site in enumerate(rows):
            await pool.enqueue_job("shoot_site", site.id, _defer_by=timedelta(seconds=i * 12))
        await pool.close()
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(503, f"Coda non raggiungibile: {ex}")

    return {"queued": len(rows), "eta_min": max(1, round(len(rows) * 12 / 60))}
