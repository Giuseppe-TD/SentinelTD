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

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response

from ..auth import require_auth

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

CONN_DIR = "/data/connectors"
# Sorgenti inclusi nell'immagine (COPY connectors ./connectors nel Dockerfile): il pannello
# ne costruisce il pacchetto installabile al volo, cosi' non serve zippare o caricare nulla.
SRC_DIR = os.getenv("CONNECTOR_SRC_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "connectors"))
SRC_SUB = {"wp": os.path.join("wordpress", "td-panopticon"), "joomla": os.path.join("joomla", "plg_system_tdpanopticon")}
KINDS = ("joomla", "wp")
MAX_SIZE = 20 * 1024 * 1024   # 20 MB: i connettori sono nell'ordine dei KB, largo comunque


def _paths(kind: str):
    return os.path.join(CONN_DIR, f"{kind}.zip"), os.path.join(CONN_DIR, f"{kind}.json")


def _src_dir(kind: str) -> str:
    return os.path.join(SRC_DIR, SRC_SUB.get(kind, ""))


def _source_version(kind: str) -> str:
    """Versione letta dai sorgenti inclusi ('' se i sorgenti non ci sono)."""
    base = _src_dir(kind)
    try:
        if kind == "wp":
            main = os.path.join(base, "td-panopticon.php")
            m = re.search(r"^\s*\*\s*Version:\s*([\w.\-]+)", open(main, encoding="utf-8").read(), re.M)
            return m.group(1) if m else ""
        m = re.search(r"<version>([\w.\-]+)</version>", open(os.path.join(base, "tdpanopticon.xml"), encoding="utf-8").read())
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _package_name(kind: str, version: str) -> str:
    tag = "wp" if kind == "wp" else "jm"
    return f"sentinel-td-{tag}-{version or 'dev'}.zip"


def _build_from_sources(kind: str) -> bytes:
    """Zip installabile costruito dai sorgenti inclusi.

    WordPress: cartella 'td-panopticon/' dentro lo zip (WP installa la cartella).
    Joomla: manifest e cartelle alla radice dello zip (com'e' richiesto dall'installer).
    """
    base = _src_dir(kind)
    if not os.path.isdir(base):
        raise HTTPException(404, "Sorgenti del connettore non disponibili in questa installazione")
    out = io.BytesIO()
    prefix = "td-panopticon/" if kind == "wp" else ""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__MACOSX", "__pycache__")]
            for f in sorted(files):
                if f == ".DS_Store":
                    continue
                full = os.path.join(root, f)
                arc = prefix + os.path.relpath(full, base).replace(os.sep, "/")
                z.write(full, arc)
    return out.getvalue()


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
        v = _source_version(kind)
        if v:
            return {"kind": kind, "present": True, "builtin": True, "version": v,
                    "filename": _package_name(kind, v), "size": 0, "mtime": 0}
        return {"kind": kind, "present": False, "builtin": False}
    out = {"kind": kind, "present": True, "builtin": False, "size": os.path.getsize(zpath),
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


# ---------------------------------------------------------------- pacchetto personalizzato
HUB_URL_KEY = "connector:hub_url"
_PHP_URL_RE = re.compile(rb"const TDPANOP_HUB_URL = '[^']*';")
_PHP_KEY_RE = re.compile(rb"const TDPANOP_HUB_KEY = '[^']*';")


async def _hub_url() -> str:
    """Indirizzo pubblico di questo Sentinel, impostato in Connettori."""
    from ..db import SessionLocal
    from ..models import AppSetting
    async with SessionLocal() as s:
        row = await s.get(AppSetting, HUB_URL_KEY)
        return (row.value if row and row.value else "").strip().rstrip("/")


def _php_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _personalize_wp(content: bytes, hub_url: str, hub_key: str) -> bytes:
    """Scrive indirizzo e chiave nelle costanti del connettore WordPress.

    Il sorgente pubblicato e' neutro (costanti vuote) e chiunque puo' compilarle dal
    backend del sito; qui prepariamo la copia gia' configurata per QUESTO pannello,
    cosi' i siti nuovi si collegano da soli senza incollare nulla.
    """
    hub_url = (hub_url or "").strip().rstrip("/")
    src = io.BytesIO(content)
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".php") and _PHP_URL_RE.search(data):
                data = _PHP_URL_RE.sub(("const TDPANOP_HUB_URL = '%s';" % _php_quote(hub_url)).encode(), data, count=1)
                data = _PHP_KEY_RE.sub(("const TDPANOP_HUB_KEY = '%s';" % _php_quote(hub_key)).encode(), data, count=1)
            zout.writestr(item, data)
    return out.getvalue()


@router.get("/hub-url", dependencies=[Depends(require_auth)])
async def get_hub_url():
    return {"url": await _hub_url()}


@router.put("/hub-url", dependencies=[Depends(require_auth)])
async def set_hub_url(payload: dict = Body(...)):
    from ..db import SessionLocal
    from ..models import AppSetting
    url = str(payload.get("url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(422, "L'indirizzo deve iniziare con http:// o https://")
    if len(url) > 300:
        raise HTTPException(422, "Indirizzo troppo lungo")
    async with SessionLocal() as s:
        row = await s.get(AppSetting, HUB_URL_KEY)
        if row:
            row.value = url
        else:
            s.add(AppSetting(key=HUB_URL_KEY, value=url))
        await s.commit()
    return {"url": url}


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
async def download_connector(kind: str, plain: str = Query("")):
    """Download dello zip del connettore.

    Richiede il JWT pieno nell'header, NON il token immagine in query string: lo zip
    del connettore WordPress contiene la chiave di registrazione (TDPANOP_HUB_KEY), e
    i token in query finiscono in cronologia, log del proxy e header Referer.
    Il frontend lo scarica via fetch + blob."""
    if kind not in KINDS:
        raise HTTPException(400, "kind non valido")
    meta = _meta(kind)
    name = meta.get("filename") or f"{kind}.zip"
    zpath, _ = _paths(kind)
    if os.path.isfile(zpath):
        with open(zpath, "rb") as f:
            content = f.read()
    else:
        content = _build_from_sources(kind)     # nessun caricamento: costruisci dai sorgenti

    # WordPress: se l'indirizzo del pannello e' configurato, consegna una copia gia'
    # personalizzata (indirizzo + chiave di registrazione). Lo zip in archivio resta
    # quello neutro caricato dall'amministratore.
    if kind == "wp" and plain != "1":
        hub = await _hub_url()
        if hub:
            from ..db import SessionLocal
            from .agent import get_register_key
            async with SessionLocal() as s:
                key = await get_register_key(s)
            try:
                content = _personalize_wp(content, hub, key or "")
            except Exception as ex:  # noqa: BLE001
                raise HTTPException(500, f"Personalizzazione non riuscita: {ex}")

    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/{kind}", dependencies=[Depends(require_auth)])
async def reset_connector(kind: str):
    """Rimuove lo zip caricato: si torna al pacchetto incluso nell'installazione."""
    if kind not in KINDS:
        raise HTTPException(400, "kind non valido")
    zpath, jpath = _paths(kind)
    for p in (zpath, jpath):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    return _meta(kind)
