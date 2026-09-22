"""Impostazioni operative modificabili dalla UI (mai segreti/token)."""
import json
from copy import deepcopy

from .db import SessionLocal
from .models import AppSetting

KEY = "ui:operational_settings"
DEFAULTS = {
    "domain_alert_days": [30, 14, 7],
    "component_alert_days": [30, 14, 7],
    "domain_scan_days": 7,
    "domain_parallel_lookups": 4,
    "expiry_warning_days": 30,
    "expiry_critical_days": 7,
    "screenshot_every_hours": 12,   # ogni quante ore rigenerare l'anteprima dei siti
    "history_retention_days": 400,  # cronologia dettagliata degli update (report dettagliato)
}


def _days_list(value, fallback):
    try:
        vals = sorted({int(x) for x in value if 1 <= int(x) <= 3650}, reverse=True)
        return vals[:8] or list(fallback)
    except Exception:
        return list(fallback)


def normalize(data: dict | None) -> dict:
    src = data or {}
    out = deepcopy(DEFAULTS)
    out["domain_alert_days"] = _days_list(src.get("domain_alert_days", out["domain_alert_days"]), DEFAULTS["domain_alert_days"])
    out["component_alert_days"] = _days_list(src.get("component_alert_days", out["component_alert_days"]), DEFAULTS["component_alert_days"])
    for key, lo, hi in (
        ("domain_scan_days", 1, 90),
        ("domain_parallel_lookups", 1, 8),
        ("expiry_warning_days", 1, 3650),
        ("expiry_critical_days", 1, 3650),
        ("screenshot_every_hours", 1, 720),
        ("history_retention_days", 7, 3650),
    ):
        try:
            out[key] = max(lo, min(hi, int(src.get(key, out[key]))))
        except Exception:
            pass
    if out["expiry_critical_days"] > out["expiry_warning_days"]:
        out["expiry_critical_days"] = out["expiry_warning_days"]
    return out


async def get_operational_settings() -> dict:
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, KEY)
            if row and row.value:
                return normalize(json.loads(row.value))
    except Exception:
        pass
    return deepcopy(DEFAULTS)


async def save_operational_settings(data: dict) -> dict:
    clean = normalize(data)
    async with SessionLocal() as s:
        row = await s.get(AppSetting, KEY)
        value = json.dumps(clean, separators=(",", ":"))
        if row:
            row.value = value
        else:
            s.add(AppSetting(key=KEY, value=value))
        await s.commit()
    return clean
