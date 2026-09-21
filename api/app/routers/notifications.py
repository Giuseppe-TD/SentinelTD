"""
API for Sentinel notification templates.
The editor language follows X-UI-Language; background delivery follows
DEFAULT_UI_LANGUAGE unless the administrator saved a custom template.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Body, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AppSetting
from ..auth import require_auth
from .. import notify
from ..i18n import DEFAULT_LANGUAGE, normalize_language

router = APIRouter(prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(require_auth)])


def _lang(request: Request) -> str:
    return normalize_language(request.headers.get("x-ui-language"), DEFAULT_LANGUAGE)


@router.get("")
async def list_events(request: Request):
    """All events with effective config, localized defaults and available variables."""
    lang = _lang(request)
    out = []
    for key in notify.EVENTS:
        meta = notify.event_meta(key, lang)
        cfg = await notify.get_config(key, lang)
        out.append({
            "event": key,
            "label": meta["label"],
            "desc": meta["desc"],
            "vars": meta["vars"],
            "config": cfg,
            "default": notify.default_config(key, lang),
        })
    return out


@router.put("/{event}")
async def save_event(event: str, request: Request, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    if event not in notify.EVENTS:
        raise HTTPException(404, "Evento sconosciuto")
    lang = _lang(request)
    allowed = {"enabled", "email", "telegram", "subject", "body_email", "body_telegram"}
    cfg = {k: payload[k] for k in allowed if k in payload}
    test = notify.render(event, {**notify.default_config(event, lang), **cfg}, notify.sample_context(event, lang), lang)
    if test["error"]:
        raise HTTPException(422, f"Template non valido — {test['error']}")
    row = await s.get(AppSetting, f"notif:{event}")
    if row:
        row.value = json.dumps(cfg)
    else:
        s.add(AppSetting(key=f"notif:{event}", value=json.dumps(cfg)))
    await s.commit()
    return {"ok": True, "config": await notify.get_config(event, lang)}


@router.post("/{event}/reset")
async def reset_event(event: str, request: Request, s: AsyncSession = Depends(get_session)):
    if event not in notify.EVENTS:
        raise HTTPException(404, "Evento sconosciuto")
    lang = _lang(request)
    row = await s.get(AppSetting, f"notif:{event}")
    if row:
        await s.delete(row)
        await s.commit()
    return {"ok": True, "config": notify.default_config(event, lang)}


@router.post("/{event}/preview")
async def preview_event(event: str, request: Request, payload: dict = Body(default={})):
    """Render the unsaved editor contents using localized sample data."""
    if event not in notify.EVENTS:
        raise HTTPException(404, "Evento sconosciuto")
    lang = _lang(request)
    cfg = {
        **notify.default_config(event, lang),
        **{k: v for k, v in payload.items() if k in ("subject", "body_email", "body_telegram")},
    }
    return notify.render(event, cfg, notify.sample_context(event, lang), lang)


@router.post("/{event}/test")
async def test_event(event: str, request: Request, payload: dict = Body(default={})):
    """Really send the sample event using the current editor language."""
    if event not in notify.EVENTS:
        raise HTTPException(404, "Evento sconosciuto")
    lang = _lang(request)
    cfg = await notify.get_config(event, lang)
    for k in ("subject", "body_email", "body_telegram"):
        if k in payload:
            cfg[k] = payload[k]
    if "email" in payload:
        cfg["email"] = bool(payload["email"])
    if "telegram" in payload:
        cfg["telegram"] = bool(payload["telegram"])
    cfg["enabled"] = True
    r = notify.render(event, cfg, notify.sample_context(event, lang), lang)
    if r["error"]:
        raise HTTPException(422, f"Template non valido — {r['error']}")
    sent = {"email": False, "telegram": False, "error": ""}
    if cfg["email"]:
        try:
            await notify.send_report(r["subject"], r["body_email"])
            sent["email"] = True
        except Exception as ex:  # noqa: BLE001
            sent["error"] = f"email: {ex}"
    if cfg["telegram"]:
        try:
            sent["telegram"] = bool(await notify.send_telegram(r["body_telegram"]))
        except Exception as ex:  # noqa: BLE001
            sent["error"] += f" telegram: {ex}"
    return sent
