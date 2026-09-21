"""
Archivio connettori (Joomla / WP).

Due slot fissi: 'joomla' (plg_system_tdpanopticon) e 'wp' (td-panopticon). Upload di uno
zip per slot, download quando serve (es. nuovo sito da agganciare o redeploy dopo un
aggiornamento del connettore). La versione viene estratta automaticamente dallo zip:
 - joomla -> tdpanopticon.xml, tag <version>
 - wp     -> td-panopticon.php, header "Version: x.y.z"

Storage su /data/connectors (volume docker). Metadati in un .json accanto allo zip.
Upload autenticato con la sessione admin; download con il token immagini in query
(stesso meccanismo degli screenshot: il browser deve poter scaricare con un link diretto).
"""
import io
import json
import os
import re
import time
import zipfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse

from ..auth import require_auth

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

CONN_DIR = "/data/connectors"
KINDS = ("joomla", "wp")
MAX_SIZE = 20 * 1024 * 1024   # 20 MB: i connettori sono nell'ordine dei KB, largo comunque


def _paths(kind: str):
    return os.path.join(CONN_DIR, f"{kind}.zip"), os.path.join(CONN_DIR, f"{kind}.json")


def _extract_version(kind: str, content: bytes) -> str:
    """Versione dal contenuto dello zip. '' se non trovata (upload comunque accettato)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = zf.namelist()
        if kind == "joomla":
            # manifest del plugin: tdpanopticon.xml (a root o in una sottocartella)
            for n in names:
                if os.path.basename(n).lower() == "tdpanopticon.xml":
                    m = re.search(r"<version>\s*([0-9][0-9.]*)\s*</version>", zf.read(n).decode("utf-8", "ignore"))
                    if m:
                        return m.group(1)
        else:
            # header del plugin WP: td-panopticon.php
            for n in names:
                if os.path.basename(n).lower() == "td-panopticon.php":
                    m = re.search(r"Version:\s*([0-9][0-9.]*)", zf.read(n).decode("utf-8", "ignore"))
                    if m:
                        return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _meta(kind: str) -> dict:
    zpath, jpath = _paths(kind)
    if not os.path.isfile(zpath):
        return {"kind": kind, "present": False}
    out = {"kind": kind, "present": True, "size": os.path.getsize(zpath),
           "mtime": int(os.path.getmtime(zpath)), "version": "", "filename": f"{kind}.zip"}
    try:
        with open(jpath, encoding="utf-8") as f:
            saved = json.load(f)
        out["version"] = saved.get("version", "")
        out["filename"] = saved.get("filename", out["filename"])
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("", dependencies=[Depends(require_auth)])
async def list_connectors():
    return [_meta(k) for k in KINDS]


@router.get("/regkey", dependencies=[Depends(require_auth)])
async def show_register_key():
    """Chiave di registrazione per l'auto-collegamento dei connettori (generata al primo uso)."""
    from ..db import SessionLocal
    from .agent import get_register_key
    async with SessionLocal() as s:
        return {"key": await get_register_key(s)}


@router.post("/regkey/rotate", dependencies=[Depends(require_auth)])
async def rotate_register_key_ep():
    """Rigenera la chiave: i connettori non ancora collegati dovranno usare quella nuova."""
    from ..db import SessionLocal
    from .agent import rotate_register_key
    async with SessionLocal() as s:
        return {"key": await rotate_register_key(s)}


@router.post("/{kind}", dependencies=[Depends(require_auth)])
async def upload_connector(kind: str, package: UploadFile = File(...)):
    if kind not in KINDS:
        raise HTTPException(400, "kind deve essere 'joomla' o 'wp'")
    content = await package.read()
    if not content:
        raise HTTPException(400, "File vuoto")
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "File troppo grande")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise HTTPException(400, "Il file non è uno zip valido")

    version = _extract_version(kind, content)
    os.makedirs(CONN_DIR, exist_ok=True)
    zpath, jpath = _paths(kind)
    with open(zpath, "wb") as f:
        f.write(content)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"version": version, "filename": package.filename or f"{kind}.zip",
                   "uploaded_at": int(time.time())}, f)
    return _meta(kind)


@router.get("/{kind}/download", dependencies=[Depends(require_auth)])
async def download_connector(kind: str):
    """Download dello zip del connettore.

    Richiede il JWT pieno nell'header, NON il token immagine in query string: lo zip
    del connettore WordPress contiene la chiave di registrazione (TDPANOP_HUB_KEY), e
    i token in query finiscono in cronologia, log del proxy e header Referer.
    Il frontend lo scarica via fetch + blob."""
    if kind not in KINDS:
        raise HTTPException(400, "kind non valido")
    zpath, _ = _paths(kind)
    if not os.path.isfile(zpath):
        raise HTTPException(404, "Connettore non ancora caricato")
    meta = _meta(kind)
    return FileResponse(zpath, media_type="application/zip", filename=meta.get("filename") or f"{kind}.zip")
