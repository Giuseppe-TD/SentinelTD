"""
Mass install plugin/tema.

Riceve UNO zip + l'elenco dei siti selezionati e lo installa (e attiva) su ciascuno,
parlando con lo stesso connettore usato per status/update:
 - WP     -> POST {url}/wp-json/tdpanopticon/v1/install   (multipart: package, kind, activate)
 - Joomla -> POST {url}/index.php?option=com_ajax&plugin=tdpanopticon&group=system&format=json
                  &task=install&activate=N                (multipart: package; l'Installer
                  rileva il tipo dal manifest, quindi 'kind' non serve)

Lo zip viene letto UNA volta in memoria e riusato per ogni sito (uso interno, file
nell'ordine dei MB: ok). Ogni sito ha il suo esito, gli errori non bloccano gli altri.
"""
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Site, Extension
from ..auth import require_auth
from ..connectors import wp_rest_url

router = APIRouter(prefix="/api/install", tags=["install"], dependencies=[Depends(require_auth)])

# install di un singolo sito: puo' durare (download/unzip/copia file). Generoso.
_INSTALL_TIMEOUT = 300.0


def _unwrap_joomla(payload):
    """com_ajax wrappa in {"success":bool,"data":[...]}. Gestisco anche risposte piatte."""
    if isinstance(payload, dict) and "success" in payload:
        if not payload.get("success"):
            return {"ok": False, "error": payload.get("message") or "com_ajax error (token/plugin?)"}
        data = payload.get("data") or []
        return data[0] if data else {"ok": False, "error": "risposta com_ajax vuota"}
    if isinstance(payload, list):
        return payload[0] if payload else {"ok": False, "error": "risposta com_ajax vuota"}
    if isinstance(payload, dict):
        return payload
    return {"ok": False, "error": "formato risposta Joomla non valido"}


async def _install_one(site: Site, content: bytes, filename: str, kind: str, activate: bool) -> dict:
    """Invia lo zip al connettore del sito. Ritorna l'esito normalizzato (non solleva)."""
    headers = {
        "Authorization": f"Bearer {site.token}",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    cb = int(time.time())
    files = {"package": (filename, content, "application/zip")}
    try:
        async with httpx.AsyncClient(timeout=_INSTALL_TIMEOUT, follow_redirects=True) as client:
            if site.cms == "wp":
                r = await client.post(
                    wp_rest_url(site, "install", f"_={cb}"),
                    headers=headers,
                    data={"kind": kind, "activate": "1" if activate else "0"},
                    files=files,
                )
                r.raise_for_status()
                d = r.json()
            else:  # joomla
                params = {
                    "option": "com_ajax", "plugin": "tdpanopticon", "group": "system",
                    "format": "json", "task": "install",
                    "activate": "1" if activate else "0", "_": cb,
                }
                r = await client.post(
                    f"{site.url.rstrip('/')}/index.php",
                    headers=headers, params=params, files=files,
                )
                r.raise_for_status()
                d = _unwrap_joomla(r.json())
        return {
            "ok": bool(d.get("ok")),
            "error": str(d.get("error", "")),
            "name": str(d.get("name", "")),
            "slug": str(d.get("slug", "")),
            "new": str(d.get("new", "")),
            "type": str(d.get("type", "")),
            "activated": bool(d.get("activated", False)),
        }
    except httpx.HTTPStatusError as ex:
        body = ""
        try:
            body = ex.response.text[:200]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"HTTP {ex.response.status_code} {body}".strip()}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout: il sito ci mette troppo a installare"}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300]}


@router.post("")
async def mass_install(
    package: UploadFile = File(...),
    cms: str = Form(...),                 # 'wp' | 'joomla'
    kind: str = Form("plugin"),           # 'plugin' | 'theme' (usato solo da WP)
    activate: bool = Form(True),
    site_ids: str = Form(...),            # CSV di id: "3,7,12"
    s: AsyncSession = Depends(get_session),
):
    if cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    if kind not in ("plugin", "theme"):
        raise HTTPException(422, "kind deve essere 'plugin' o 'theme'")

    fname = (package.filename or "package.zip")
    if not fname.lower().endswith(".zip"):
        raise HTTPException(422, "Il pacchetto deve essere un file .zip")

    try:
        ids = [int(x) for x in str(site_ids).split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "site_ids non valido")
    if not ids:
        raise HTTPException(422, "Nessun sito selezionato")

    rows = (await s.execute(select(Site).where(Site.id.in_(ids)))).scalars().all()
    # rispetta il cms scelto: ignora eventuali siti dell'altro cms finiti per errore nella lista
    targets = [r for r in rows if r.cms == cms]
    if not targets:
        raise HTTPException(422, f"Nessun sito {cms} tra quelli selezionati")

    content = await package.read()   # una sola lettura, riusata per ogni sito
    if not content:
        raise HTTPException(422, "Pacchetto vuoto")

    results = []
    for site in targets:
        res = await _install_one(site, content, fname, kind, activate)
        results.append({
            "site_id": site.id,
            "site_name": site.name,
            "url": site.url,
            **res,
        })

    ok_n = sum(1 for r in results if r["ok"])
    return {
        "filename": fname,
        "cms": cms,
        "kind": kind,
        "activate": activate,
        "total": len(results),
        "ok": ok_n,
        "failed": len(results) - ok_n,
        "results": results,
    }


async def _uninstall_one(site: Site, ext_type: str, slug: str) -> dict:
    """Chiede al connettore di rimuovere un'estensione. Ritorna l'esito (non solleva)."""
    headers = {
        "Authorization": f"Bearer {site.token}",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    cb = int(time.time())
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            if site.cms == "wp":
                r = await client.post(
                    wp_rest_url(site, "uninstall", f"_={cb}"),
                    headers={**headers, "Content-Type": "application/json"},
                    json={"type": ext_type, "slug": slug},
                )
                r.raise_for_status()
                d = r.json()
            else:  # joomla
                params = {
                    "option": "com_ajax", "plugin": "tdpanopticon", "group": "system",
                    "format": "json", "task": "uninstall",
                    "extype": ext_type, "slug": slug, "_": cb,
                }
                r = await client.post(f"{site.url.rstrip('/')}/index.php", headers=headers, params=params)
                r.raise_for_status()
                d = _unwrap_joomla(r.json())
        return {
            "ok": bool(d.get("ok")),
            "error": str(d.get("error", "")),
            "name": str(d.get("name", "")),
            "slug": str(d.get("slug", slug)),
            "type": str(d.get("type", ext_type)),
            "removed": bool(d.get("removed", False)),
        }
    except httpx.HTTPStatusError as ex:
        return {"ok": False, "error": f"HTTP {ex.response.status_code}"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout"}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300]}


@router.post("/remove")
async def mass_uninstall(
    cms: str = Body(...),                 # 'wp' | 'joomla'
    items: list[dict] = Body(...),        # [{"type": "...", "slug": "..."}]
    site_ids: list[int] = Body(...),
    s: AsyncSession = Depends(get_session),
):
    if cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    if not items:
        raise HTTPException(422, "Nessuna estensione selezionata")
    if not site_ids:
        raise HTTPException(422, "Nessun sito selezionato")

    # normalizza/valida gli item
    clean = []
    for it in items:
        t = str((it or {}).get("type", "")).strip()
        sl = str((it or {}).get("slug", "")).strip()
        if t and sl:
            clean.append({"type": t, "slug": sl})
    if not clean:
        raise HTTPException(422, "Estensioni non valide")

    # i package per primi: rimuovendo il contenitore spariscono i figli (che poi
    # risulteranno "non trovati", innocuo). Evita di fallire sui figli protetti dal package.
    clean.sort(key=lambda it: 0 if it["type"] == "package" else 1)

    rows = (await s.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all()
    targets = [r for r in rows if r.cms == cms]
    if not targets:
        raise HTTPException(422, f"Nessun sito {cms} tra quelli selezionati")

    # Quali (sito, estensione) esistono DAVVERO secondo l'ultimo check: la rimozione va
    # chiesta solo a chi ce l'ha. Prima si mandava a tutti i siti selezionati, e quelli
    # senza l'estensione rispondevano "non trovata" -> errori certi nel mass uninstall.
    have = set()
    ext_rows = (await s.execute(
        select(Extension.site_id, Extension.type, Extension.slug)
        .where(Extension.site_id.in_([t.id for t in targets]))
    )).all()
    for sid, et, esl in ext_rows:
        have.add((sid, (et or "").lower(), (esl or "").lower()))

    results = []
    for site in targets:
        for it in clean:
            key = (site.id, it["type"].lower(), it["slug"].lower())
            if key not in have:
                # non installata su questo sito: niente chiamata, niente errore
                results.append({
                    "site_id": site.id, "site_name": site.name, "url": site.url,
                    "req_type": it["type"], "req_slug": it["slug"],
                    "ok": True, "skipped": True, "error": "",
                    "message": "non presente su questo sito: saltato",
                })
                continue
            res = await _uninstall_one(site, it["type"], it["slug"])
            results.append({
                "site_id": site.id, "site_name": site.name, "url": site.url,
                "req_type": it["type"], "req_slug": it["slug"],
                **res,
            })

    done = [r for r in results if not r.get("skipped")]
    ok_n = sum(1 for r in done if r["ok"])
    return {
        "cms": cms, "items": clean,
        "total": len(done), "ok": ok_n, "failed": len(done) - ok_n,
        "skipped": len(results) - len(done),
        "results": results,
    }


# slug del connettore stesso, per CMS: da segnalare come protetto nella UI
# Slug del connettore stesso: va protetto dalla rimozione. Su WordPress lo slug e'
# il nome della CARTELLA, e il connettore puo' stare in "td-panopticon" (parco storico)
# oppure in "sentinel-td" (nuove installazioni): vanno protetti entrambi.
_SELF_SLUG = {"wp": {"td-panopticon", "sentinel-td"}, "joomla": {"tdpanopticon"}}


@router.get("/search")
async def search_extensions(
    cms: str = Query(...),
    q: str = Query(...),
    s: AsyncSession = Depends(get_session),
):
    """Cerca tra le estensioni gia' note (ultimo check) dei siti del CMS scelto.
    Aggrega per (type, slug): mostra su quali siti e' presente. Nessuna chiamata ai siti."""
    if cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    term = q.strip()
    if len(term) < 2:
        raise HTTPException(422, "Inserisci almeno 2 caratteri")

    like = f"%{term}%"
    rows = (await s.execute(
        select(Extension, Site)
        .join(Site, Extension.site_id == Site.id)
        .where(Site.cms == cms, or_(Extension.name.ilike(like), Extension.slug.ilike(like)))
    )).all()

    agg: dict[tuple[str, str], dict] = {}
    for ext, site in rows:
        key = (ext.type, ext.slug)
        if key not in agg:
            agg[key] = {
                "type": ext.type, "slug": ext.slug, "name": ext.name,
                "site_ids": [], "sites": [],
                "protected": (ext.slug in _SELF_SLUG.get(cms, set())),
            }
        agg[key]["site_ids"].append(site.id)
        agg[key]["sites"].append(site.name)
        # dettaglio per sito: versione installata (e se ha un update pendente),
        # cosi' dalla modale si vede QUALI siti e A CHE VERSIONE, non solo quanti
        agg[key].setdefault("site_details", []).append({
            "id": site.id,
            "name": site.name,
            "version": ext.current_version or "",
            "update_available": bool(ext.update_available),
            "new_version": ext.new_version or "",
        })

    def _vkey(v: str):
        return [int(x) if x.isdigit() else x for x in (v or "0").replace("-", ".").split(".")]

    results = sorted(agg.values(), key=lambda r: (r["type"], (r["name"] or "").lower()))
    for r in results:
        r["count"] = len(r["site_ids"])
        r["site_details"].sort(key=lambda d: d["name"].lower())
        # versioni distinte installate (ordinate): 1 sola = parco allineato, >1 = versioni miste
        vers = sorted({d["version"] for d in r["site_details"] if d["version"]}, key=_vkey)
        r["versions"] = vers
    return {"cms": cms, "q": term, "results": results}
