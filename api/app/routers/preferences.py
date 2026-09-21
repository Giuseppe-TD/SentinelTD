"""Impostazioni operative esposte alla UI: nessun token/password/SMTP."""
from fastapi import APIRouter, Body, Depends

from ..auth import require_auth
from ..settings_store import get_operational_settings, save_operational_settings

router = APIRouter(prefix="/api/preferences", tags=["preferences"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_preferences():
    return await get_operational_settings()


@router.put("")
async def put_preferences(payload: dict = Body(...)):
    return await save_operational_settings(payload)
