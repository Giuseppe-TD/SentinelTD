"""Branding della UI: usa gli asset inclusi nel pacchetto oppure override persistenti."""
from pathlib import Path
import mimetypes
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..db import get_session
from ..models import AppSetting

router = APIRouter(prefix="/api/branding", tags=["branding"])

BRAND_DIR = Path("/data/branding")
DEFAULT_LOGO = Path("static/logo.png")
DEFAULT_FAVICON = Path("static/favicon.png")
MAX_BYTES = 4 * 1024 * 1024
ALLOWED = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}


async def _setting(s: AsyncSession, key: str) -> str:
    row = await s.get(AppSetting, key)
    return row.value if row else ""


async def _asset(s: AsyncSession, kind: str) -> tuple[Path, str, bool]:
    key = f"brand:{kind}_path"
    custom = await _setting(s, key)
    if custom:
        p = Path(custom)
        if p.is_file() and BRAND_DIR in p.parents:
            mime = await _setting(s, f"brand:{kind}_mime") or mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            return p, mime, True
    p = DEFAULT_LOGO if kind == "logo" else DEFAULT_FAVICON
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    return p, mime, False


@router.get("")
async def get_branding(s: AsyncSession = Depends(get_session)):
    logo, _, custom_logo = await _asset(s, "logo")
    fav, _, custom_fav = await _asset(s, "favicon")
    lv = int(logo.stat().st_mtime) if logo.exists() else int(time.time())
    fv = int(fav.stat().st_mtime) if fav.exists() else int(time.time())
    return {
        "logo_url": f"/api/branding/logo?v={lv}",
        "favicon_url": f"/api/branding/favicon?v={fv}",
        "custom_logo": custom_logo,
        "custom_favicon": custom_fav,
    }


@router.get("/logo")
async def logo(s: AsyncSession = Depends(get_session)):
    p, mime, _ = await _asset(s, "logo")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type=mime, headers={"Cache-Control": "public, max-age=300"})


@router.get("/favicon")
async def favicon(s: AsyncSession = Depends(get_session)):
    p, mime, _ = await _asset(s, "favicon")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type=mime, headers={"Cache-Control": "public, max-age=300"})


@router.post("/{kind}")
async def upload_branding(
    kind: str,
    file: UploadFile = File(...),
    s: AsyncSession = Depends(get_session),
    _=Depends(require_auth),
):
    if kind not in ("logo", "favicon"):
        raise HTTPException(404)
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED:
        raise HTTPException(415, "Formato non supportato: usa PNG, JPG, WEBP, SVG o ICO")
    content = await file.read(MAX_BYTES + 1)
    if not content:
        raise HTTPException(422, "File vuoto")
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Immagine troppo grande (max 4 MB)")

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for old in BRAND_DIR.glob(f"{kind}.*"):
        old.unlink(missing_ok=True)
    path = BRAND_DIR / f"{kind}{ALLOWED[mime]}"
    path.write_bytes(content)

    values = {
        f"brand:{kind}_path": str(path),
        f"brand:{kind}_mime": mime,
    }
    for key, value in values.items():
        row = await s.get(AppSetting, key)
        if row:
            row.value = value
        else:
            s.add(AppSetting(key=key, value=value))
    await s.commit()
    return await get_branding(s)


@router.delete("/{kind}")
async def reset_branding(kind: str, s: AsyncSession = Depends(get_session), _=Depends(require_auth)):
    if kind not in ("logo", "favicon"):
        raise HTTPException(404)
    custom, _, _ = await _asset(s, kind)
    if custom.exists() and BRAND_DIR in custom.parents:
        custom.unlink(missing_ok=True)
    for key in (f"brand:{kind}_path", f"brand:{kind}_mime"):
        row = await s.get(AppSetting, key)
        if row:
            await s.delete(row)
    await s.commit()
    return await get_branding(s)
