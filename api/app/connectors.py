"""
Interroga il connettore installato sul sito e normalizza la risposta.

Contratto JSON atteso (uguale per WP e Joomla):
{
  "cms": "wp" | "joomla",
  "core": {"current": "6.7.1", "latest": "6.7.1", "update": false},
  "php": "8.2.10",
  "extensions": [
     {"type": "plugin", "name": "WooCommerce", "slug": "woocommerce",
      "current": "9.4.0", "new": "9.5.1", "update": true},
     ...
  ]
}
Vengono restituite TUTTE le estensioni installate (update true/false); i contatori
per categoria li calcola qui il backend.
"""
import time
import re
import httpx
from datetime import datetime, timezone
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Site, Extension


# Package "vendor" che NON pubblicano gli update sul canale del CMS (li rileva vendor_scan
# dal file API del vendore). Vanno preservati attraverso il refresh di apply_status e
# contati nei contatori del sito, altrimenti il pending sparisce a ogni check e la card
# non mostra la barra gialla.
VENDOR_SLUGS = {"pkg_BaForms", "pkg_Gallery"}


def _vgt(a: str, b: str) -> bool:
    """True se la versione a e' piu' recente di b (confronto numerico per componenti)."""
    pa = [int(x) for x in re.findall(r"\d+", a or "")]
    pb = [int(x) for x in re.findall(r"\d+", b or "")]
    return pa > pb


def _category(ext_type: str) -> str:
    """Mappa il tipo di estensione (WP/Joomla) in: plugin | theme | other."""
    t = (ext_type or "").lower()
    if t == "plugin":
        return "plugin"
    if t in ("theme", "template"):
        return "theme"
    return "other"  # component | module | library | ...


# forma dell'endpoint REST WP per sito: "wpjson" (pretty, /wp-json/...) oppure
# "restroute" (index.php?rest_route=..., unica forma che funziona coi permalink
# "semplici"). Il fetch prova la preferita e, se fallisce, l'altra: quella buona
# viene ricordata qui (in-process; al riavvio si ri-scopre da sola al primo check).
_WP_REST_STYLE: dict[int, str] = {}


def wp_rest_url(site: Site, path: str, qs: str = "") -> str:
    """URL dell'endpoint REST del connettore WP nello stile giusto per il sito."""
    base = site.url.rstrip("/")
    style = _WP_REST_STYLE.get(site.id, "wpjson")
    if style == "restroute":
        url = f"{base}/index.php?rest_route=/tdpanopticon/v1/{path}"
        return url + (("&" + qs) if qs else "")
    url = f"{base}/wp-json/tdpanopticon/v1/{path}"
    return url + (("?" + qs) if qs else "")


async def fetch_status(site: Site, timeout: float = 20.0, force: bool = False) -> dict:
    """GET autenticata verso il connettore. Solleva eccezione su errore.

    force=False (default): CHECK PASSIVO. Il connettore legge i dati di update gia'
        presenti nel CMS (tabella #__updates su Joomla, transient su WP), mantenuti
        freschi dal cron/wp-cron del sito. Impatto minimo, nessuna richiesta in uscita
        dal sito monitorato. E' la modalita' usata dai check automatici schedulati.
    force=True: REFRESH ON-DEMAND. Il connettore contatta i server di update e ripopola
        i dati prima di rispondere (piu' pesante). Usato solo dal pulsante 'Check' manuale
        in dashboard, come l'icona reload di Akeeba Panopticon.
    """
    # cache-buster: timestamp univoco + header no-cache. Evita che reverse proxy
    # (es. NPMplus) servano risposte cachate, senza dover configurare ogni sito.
    cb = int(time.time())
    if site.cms == "wp":
        refresh = "&refresh=1" if force else ""
        endpoint = wp_rest_url(site, "status", f"_={cb}" + refresh)
    else:  # joomla
        task = "refresh" if force else "status"
        endpoint = f"{site.url.rstrip('/')}/index.php?option=com_ajax&plugin=tdpanopticon&group=system&format=json&task={task}&_={cb}"
    headers = {
        "Authorization": f"Bearer {site.token}",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            r = await client.get(endpoint, headers=headers)
            r.raise_for_status()
            payload = r.json()
        except Exception:  # noqa: BLE001
            if site.cms != "wp":
                raise
            # WP: la forma REST giusta dipende dal sito (permalink/proxy). Se quella
            # preferita fallisce, prova l'ALTRA; se funziona, ricordala per i prossimi
            # check e per update/install. Cosi' il pannello lavora con qualsiasi sito
            # senza configurare niente.
            cur = _WP_REST_STYLE.get(site.id, "wpjson")
            _WP_REST_STYLE[site.id] = "restroute" if cur == "wpjson" else "wpjson"
            refresh = "&refresh=1" if force else ""
            alt = wp_rest_url(site, "status", f"_={cb}" + refresh)
            r = await client.get(alt, headers=headers)
            r.raise_for_status()
            payload = r.json()

    # WordPress: payload diretto. Joomla com_ajax: {"success":bool,"data":[ {...} ]}
    if site.cms == "joomla":
        # gestisco sia il wrapping com_ajax sia risposte gia' piatte (robustezza)
        if isinstance(payload, dict) and "success" in payload:
            if not payload.get("success"):
                raise RuntimeError(payload.get("message") or "com_ajax error (token/plugin?)")
            data = payload.get("data") or []
            if not data:
                raise RuntimeError("Risposta com_ajax vuota")
            return data[0]
        if isinstance(payload, list):
            if not payload:
                raise RuntimeError("Risposta com_ajax vuota")
            return payload[0]
        if isinstance(payload, dict):
            return payload          # gia' piatta
        raise RuntimeError("Formato risposta Joomla non valido")
    return payload


async def apply_status(session: AsyncSession, site: Site, force: bool = False) -> None:
    """Polla il sito e scrive lo stato a DB. Non solleva: registra l'errore sul Site.
    force=True forza il refresh lato connettore (vedi fetch_status)."""
    try:
        # timeout proporzionato: il check passivo deve essere reattivo (20s), ma con
        # force=True il connettore esegue sul sito il refresh COMPLETO (wp_version_check +
        # wp_update_plugins + wp_update_themes in sincrono verso wordpress.org; su Joomla il
        # rebuild dei canali): su un sito carico (es. WooCommerce con molti plugin) supera
        # i 20s. Col timeout corto il ciclo di auto-update andava in timeout -> status err
        # -> return silenzioso PRIMA di tentare gli update: pending eternamente acceso e
        # mai processato (caso reale: core WP di shop mai aggiornato, senza errori nei log).
        data = await fetch_status(site, timeout=(120.0 if force else 20.0), force=force)
        core = data.get("core", {})
        exts = data.get("extensions", []) or []

        site.core_current = str(core.get("current", ""))
        site.core_latest = str(core.get("latest", core.get("current", "")))
        site.core_update = bool(core.get("update", False))
        site.php_version = str(data.get("php", ""))

        # snapshot completo estensioni: cancella e reinserisci TUTTE.
        # PRIMA salva lo stato di cooldown (fallimenti update) per non perderlo nel refresh.
        prev = (await session.execute(
            select(Extension).where(Extension.site_id == site.id)
        )).scalars().all()
        cooldown_map = {
            (p.type, p.slug): (p.update_failed_at, p.update_failed_version)
            for p in prev
            if p.update_failed_at is not None
        }
        # preserva lo stato "vendor" (Balbooa) rilevato da vendor_scan: il connettore non lo
        # riporta (non e' sul canale del CMS), ma va tenuto attraverso il refresh finche' e'
        # piu' recente dell'installato -> cosi' resta pending e la card mostra il giallo.
        vendor_map = {
            p.slug: (p.new_version or "")
            for p in prev
            if p.slug in VENDOR_SLUGS and p.update_available and p.new_version
        }
        # versione del COMPONENT dei prodotti vendor, dal payload appena letto dal sito:
        # e' la versione "vera" del prodotto quando il manifest del package e' incoerente.
        _VENDOR_COMP = {"pkg_BaForms": "com_baforms", "pkg_Gallery": "com_gallery"}
        vendor_comp_ver: dict[str, str] = {}
        for pkg_slug, comp_slug in _VENDOR_COMP.items():
            for e in exts:
                if str(e.get("slug", "")) == comp_slug:
                    vendor_comp_ver[pkg_slug] = str(e.get("current", ""))
                    break

        await session.execute(delete(Extension).where(Extension.site_id == site.id))
        tot = {"plugin": 0, "theme": 0, "other": 0}
        upd = {"plugin": 0, "theme": 0, "other": 0}
        for e in exts:
            cat = _category(e.get("type", ""))
            etype = str(e.get("type", ""))
            eslug = str(e.get("slug", ""))
            enew = str(e.get("new", ""))
            ecur = str(e.get("current", ""))
            is_upd = bool(e.get("update"))
            # preserva l'update vendor (Balbooa) rilevato da vendor_scan: non arriva dal
            # connettore, ma se e' piu' recente dell'installato va tenuto e CONTATO, cosi'
            # finisce in upd_other e la card in lista diventa gialla come per ogni altro update.
            if not is_upd and eslug in VENDOR_SLUGS:
                vnew = vendor_map.get(eslug, "")
                # confronto con la versione VERA del prodotto: la piu' alta tra il package e
                # il suo component (Balbooa rilascia manifest incoerenti: pkg 2.4.3 con dentro
                # com 2.4.3.2). Senza, un prodotto gia' aggiornato resterebbe pending.
                vcur = ecur
                comp_v = vendor_comp_ver.get(eslug, "")
                if comp_v and _vgt(comp_v, vcur or "0"):
                    vcur = comp_v
                if vnew and _vgt(vnew, vcur):
                    is_upd = True
                    enew = vnew

            tot[cat] += 1
            if is_upd:
                upd[cat] += 1

            # riapplica il cooldown SOLO se la versione target e' ancora la stessa che aveva fallito;
            # se e' uscita una versione nuova, il cooldown decade (riprovera').
            failed_at, failed_ver = cooldown_map.get((etype, eslug), (None, ""))
            if failed_at is not None and failed_ver != enew:
                failed_at, failed_ver = None, ""

            session.add(Extension(
                site_id=site.id,
                type=etype,
                name=str(e.get("name", "")),
                slug=eslug,
                current_version=ecur,
                new_version=enew,
                update_available=is_upd,
                dlkey_missing=bool(e.get("dlkey_missing", False)),
                update_failed_at=failed_at,
                update_failed_version=failed_ver,
            ))

        site.tot_plugins, site.upd_plugins = tot["plugin"], upd["plugin"]
        site.tot_themes, site.upd_themes = tot["theme"], upd["theme"]
        site.tot_other, site.upd_other = tot["other"], upd["other"]
        site.updates_count = upd["plugin"] + upd["theme"] + upd["other"] + (1 if site.core_update else 0)
        site.status = "ok"
        site.error = ""
        site.last_checked = datetime.now(timezone.utc)
    except httpx.HTTPStatusError as ex:
        site.status = "error"
        site.error = f"HTTP {ex.response.status_code}"
        site.last_checked = datetime.now(timezone.utc)
    except Exception as ex:  # noqa: BLE001
        site.status = "error"
        site.error = str(ex)[:480]
        site.last_checked = datetime.now(timezone.utc)