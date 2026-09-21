"""
CENTRO SICUREZZA - Raccolta vulnerabilita' e matching.

Nuovo file: api/app/security.py

Aggrega piu' fonti GRATUITE di vulnerabilita' e le incrocia con l'inventario
estensioni gia' presente nel DB (tabella extensions, popolata dai connettori):

  - CISA KEV  : catalogo vulnerabilita' ATTIVAMENTE SFRUTTATE (priorita' massima).
                Mirror GitHub per evitare i rate-limit di cisa.gov.
  - VEL Joomla: Vulnerable Extensions List ufficiale Joomla (fonte d'oro per Joomla).
                Ha un hash di verifica per non riscaricare se invariato.
  - wordpress.org plugins API: per ogni slug plugin WP dice se e' "closed" (spesso
                per motivi di sicurezza) e la versione corrente. Gratis, senza key.
  - NVD/CVE   : ricerca per keyword sugli slug (rete supplementare). Rate-limit blando
                senza API key (~5 req / 30s): interroghiamo solo gli slug distinti e
                andiamo piano.

NOTA ONESTA: nessun sistema basato su feed copre uno zero-day nel giorno zero (Balbooa
e' stata sfruttata prima che la CVE esistesse). Questo e' UN livello della difesa, da
usare INSIEME all'hardening delle cartelle upload e agli update automatici, non da solo.

Il matching su slug->vulnerabilita' e' imperfetto per natura (i nomi non combaciano mai
perfettamente): puntiamo alta copertura, non onniscienza. Preferiamo qualche falso
positivo (che vedi in dashboard e ignori) a un falso negativo silenzioso.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update as sa_update

from .db import SessionLocal
from .models import Site, Extension, Vulnerability, VulnMatch
from .config import settings

log = logging.getLogger("security")

# ---------------------------------------------------------------------------
# URL delle fonti (override possibile via env in config.py se vuoi)
# ---------------------------------------------------------------------------
KEV_URL = getattr(
    settings, "KEV_FEED_URL",
    "https://raw.githubusercontent.com/BenjiTrapp/cisa-known-vuln-scraper/main/cisa-kev.json",
)
VEL_FEED_URL = getattr(settings, "VEL_FEED_URL", "https://extensions.joomla.org/vel-feed")
VEL_VERIFY_URL = getattr(settings, "VEL_VERIFY_URL", "https://extensions.joomla.org/vel-verify")
WPORG_INFO_URL = "https://api.wordpress.org/plugins/info/1.2/"
NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

HTTP_TIMEOUT = 30.0
UA = {"User-Agent": "PanopticonLite-Security/1.0 (+monitoring)"}


# ===========================================================================
# UTILITY
# ===========================================================================
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(s: str) -> str:
    """Ripulisce testo HTML (le description VEL sono piene di <p> e entita')."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_ver(v: str) -> str:
    """
    Normalizza una stringa versione per version_compare-like.
    Estrae la prima sequenza tipo 1.2.3(.4). Gestisce sporcizia tipo '3..3.09', '< 2.4.1'.
    """
    if not v:
        return ""
    m = re.search(r"(\d+(?:\.\d+){0,3})", str(v).replace("..", "."))
    return m.group(1) if m else ""


def _ver_tuple(v: str):
    v = _norm_ver(v)
    if not v:
        return ()
    return tuple(int(x) for x in v.split("."))


def _ver_lt(a: str, b: str) -> bool:
    """True se versione a < b (installata < fixata). Se una manca, prudente: False."""
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    if not ta or not tb:
        return False
    # pad alla stessa lunghezza
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return ta < tb


def _severity_from_cvss(cvss: float) -> str:
    if cvss >= 9.0:
        return "critical"
    if cvss >= 7.0:
        return "high"
    if cvss >= 4.0:
        return "medium"
    if cvss > 0:
        return "low"
    return "unknown"


def _normalize_slug_joomla(s: str) -> str:
    """
    Normalizza uno slug Joomla per il match. Toglie il prefisso tipo (com_/plg_/mod_/tpl_)
    e abbassa, cosi' 'com_baforms' e 'baforms' matchano. Ritorna la forma 'core' senza prefisso.
    """
    s = (s or "").strip().lower()
    s = re.sub(r"^(com_|mod_|plg_|tpl_|lib_|pkg_)", "", s)
    s = re.sub(r"^(system\s*-\s*|plugin\s*)", "", s)   # 'System - Novarain Installer' -> 'novarain installer'
    return re.sub(r"[^a-z0-9]+", "", s)   # solo alfanumerico per confronto robusto


def _normalize_slug_wp(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").strip().lower())


# ===========================================================================
# FETCH DELLE FONTI  ->  lista di dict grezzi
# ===========================================================================
async def fetch_kev(client: httpx.AsyncClient) -> list[dict]:
    """
    CISA KEV. Ritorna le vuln che *potrebbero* riguardarci (filtro grezzo su Joomla/WP/
    nomi estensioni noti). Le KEV hanno vendorProject/product testuali, non slug, quindi
    il match e' per keyword nel prodotto/descrizione.
    """
    out: list[dict] = []
    try:
        r = await client.get(KEV_URL, headers=UA, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for v in data.get("vulnerabilities", []):
            vendor = (v.get("vendorProject") or "").strip()
            product = (v.get("product") or "").strip()
            desc = v.get("shortDescription") or ""
            blob = f"{vendor} {product} {desc}".lower()
            # teniamo solo cio' che ha a che fare con Joomla/WordPress/estensioni web
            if not any(k in blob for k in (
                "joomla", "wordpress", "wp ", "plugin", "extension", "theme",
                "component", "cms",
            )):
                continue
            cms = "joomla" if "joomla" in blob else ("wp" if ("wordpress" in blob or "wp " in blob) else "")
            out.append({
                "source": "kev",
                "cve_id": v.get("cveID", ""),
                "ext_ref": v.get("cveID", ""),
                "title": f"{vendor} {product}: {_clean(desc)[:200]}".strip(": "),
                # KEV non da' slug: usiamo product come chiave testuale per il match keyword
                "kev_product": f"{vendor} {product}".strip().lower(),
                "affected_slug": _normalize_slug_wp(product),   # tentativo
                "affected_type": "",
                "cms": cms,
                "version_fixed": "",   # KEV non da' la versione fixata
                "severity": "critical",   # se e' in KEV, e' grave per definizione
                "cvss": 0.0,
                "exploited_in_wild": True,
                "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                "published_at": _parse_date(v.get("dateAdded")),
            })
    except Exception as ex:  # noqa: BLE001
        log.warning("fetch_kev fallito: %s", ex)
    return out


async def fetch_vel(client: httpx.AsyncClient, last_hash: str | None) -> tuple[list[dict], str | None]:
    """
    VEL Joomla. Usa prima l'hash di verifica: se identico all'ultimo, non riscarica.
    Ritorna (items, nuovo_hash). Se invariato ritorna ([], last_hash).
    """
    try:
        # 1) hash di verifica (leggero) per evitare download inutili
        try:
            rv = await client.get(VEL_VERIFY_URL, headers=UA, timeout=HTTP_TIMEOUT)
            cur_hash = rv.text.strip().strip('"')
            if last_hash and cur_hash and cur_hash == last_hash:
                log.info("VEL invariato (hash uguale), skip download")
                return [], last_hash
        except Exception:  # noqa: BLE001
            cur_hash = None

        r = await client.get(VEL_FEED_URL, headers=UA, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = (data.get("data") or {}).get("items", []) if isinstance(data, dict) else []
        out: list[dict] = []
        for it in items:
            inst = it.get("install_data") or {}
            # nome estensione: preferisci install_data.name (piu' pulito), fallback sul title
            raw_name = inst.get("name") or it.get("title") or ""
            etype = (inst.get("type") or "").lower()   # component/plugin/module/template
            patch = _norm_ver(it.get("patch_version") or "")
            vulnver = _norm_ver(it.get("vulnerable_version") or "")
            status = (it.get("statusText") or "").lower()   # 'live' | 'resolved'
            out.append({
                "source": "vel",
                "cve_id": "",
                "ext_ref": str(it.get("id", "")),
                "title": _clean(it.get("title") or raw_name)[:400],
                "affected_slug": _normalize_slug_joomla(raw_name),
                "affected_type": etype,
                "cms": "joomla",
                "version_fixed": patch,   # vuoto se LIVE (nessun fix) -> vulnerabile a prescindere
                "vel_status": status,
                "vel_vulnver": vulnver,
                "severity": "high",   # VEL non da' CVSS; default high, KEV lo alzera' se combacia
                "cvss": 0.0,
                "exploited_in_wild": False,
                "url": it.get("update_notice") or it.get("jed") or "https://vel.joomla.org/",
                "published_at": _parse_date(it.get("modified") or it.get("created")),
            })
        return out, (cur_hash or last_hash)
    except Exception as ex:  # noqa: BLE001
        log.warning("fetch_vel fallito: %s", ex)
        return [], last_hash


async def fetch_wporg_for_slugs(client: httpx.AsyncClient, slugs: list[str]) -> list[dict]:
    """
    Per ogni slug plugin WP distinto, interroga wordpress.org.
    Se il plugin risulta 'closed' (spesso rimosso per motivi di sicurezza) lo segnaliamo.
    Interroga UNA volta per slug distinto (deduplica gia' fatta dal chiamante).
    """
    out: list[dict] = []
    for slug in slugs:
        try:
            r = await client.get(
                WPORG_INFO_URL,
                params={"action": "plugin_information", "request[slug]": slug},
                headers=UA, timeout=HTTP_TIMEOUT,
            )
            if r.status_code != 200:
                continue
            j = r.json()
            # plugin chiuso: la risposta ha 'closed': true o un errore
            closed = isinstance(j, dict) and (j.get("closed") is True or j.get("error"))
            if closed:
                reason = ""
                if isinstance(j, dict):
                    reason = _clean(str(j.get("closed_reason") or j.get("error") or ""))
                out.append({
                    "source": "wporg",
                    "cve_id": "",
                    "ext_ref": slug,
                    "title": f"Plugin '{slug}' rimosso/chiuso su wordpress.org"
                             + (f" ({reason})" if reason else "")
                             + " - possibile rimozione per motivi di sicurezza",
                    "affected_slug": _normalize_slug_wp(slug),
                    "affected_type": "plugin",
                    "cms": "wp",
                    "version_fixed": "",   # nessun fix: e' stato rimosso
                    "severity": "high",
                    "cvss": 0.0,
                    "exploited_in_wild": False,
                    "url": f"https://wordpress.org/plugins/{slug}/",
                    "published_at": None,
                })
            await asyncio.sleep(0.2)   # gentile con l'API
        except Exception as ex:  # noqa: BLE001
            log.debug("wporg slug %s: %s", slug, ex)
    return out


async def fetch_nvd_for_keywords(client: httpx.AsyncClient, keywords: list[str]) -> list[dict]:
    """
    NVD keyword search per gli slug/nomi distinti. Senza API key il rate-limit e'
    ~5 richieste/30s: andiamo piano e limitiamo il numero di keyword (le piu' rilevanti).
    Rete SUPPLEMENTARE: KEV+VEL+wporg fanno il grosso, NVD aggiunge copertura CVE generica.
    """
    out: list[dict] = []
    for kw in keywords:
        if len(kw) < 4:
            continue
        try:
            r = await client.get(
                NVD_API_URL,
                params={"keywordSearch": kw, "resultsPerPage": 20},
                headers=UA, timeout=HTTP_TIMEOUT,
            )
            if r.status_code != 200:
                await asyncio.sleep(6)
                continue
            j = r.json()
            for item in j.get("vulnerabilities", []):
                cve = item.get("cve", {})
                cid = cve.get("id", "")
                descs = cve.get("descriptions", [])
                desc = next((d["value"] for d in descs if d.get("lang") == "en"), "")
                # CVSS: prendi il punteggio piu' alto disponibile (v3.1 > v3.0 > v2)
                cvss, sev = _extract_cvss(cve.get("metrics", {}))
                out.append({
                    "source": "nvd",
                    "cve_id": cid,
                    "ext_ref": cid,
                    "title": _clean(desc)[:400],
                    "affected_slug": _normalize_slug_wp(kw),
                    "affected_type": "",
                    "cms": "",   # generico
                    "version_fixed": "",   # NVD lo ha nei CPE, ma il parsing e' complesso: lasciamo vuoto
                    "severity": sev,
                    "cvss": cvss,
                    "exploited_in_wild": False,
                    "url": f"https://nvd.nist.gov/vuln/detail/{cid}",
                    "published_at": _parse_date(cve.get("published")),
                    "nvd_keyword": kw.lower(),
                })
            await asyncio.sleep(6.5)   # rispetta il rate-limit senza key
        except Exception as ex:  # noqa: BLE001
            log.debug("nvd kw %s: %s", kw, ex)
            await asyncio.sleep(6.5)
    return out


def _extract_cvss(metrics: dict) -> tuple[float, str]:
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        arr = metrics.get(key) or []
        if arr:
            data = arr[0].get("cvssData", {})
            score = float(data.get("baseScore", 0.0) or 0.0)
            return score, _severity_from_cvss(score)
    return 0.0, "unknown"


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.replace("Z", "").split("+")[0], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
    return None


# ===========================================================================
# PERSISTENZA delle vulnerabilita' + MATCH con i siti
# ===========================================================================
async def _save_vulnerabilities(session, rows: list[dict]) -> list[Vulnerability]:
    """
    Salva le vulnerabilita' che possono generare match: solo VEL (Joomla) e wporg (WordPress),
    perche' sono le uniche con slug preciso. KEV e NVD NON vengono salvate come righe che
    matchano (genererebbero falsi positivi), ma i CVE presenti in KEV servono a marcare
    'exploited_in_wild' le vuln VEL/wporg con lo stesso CVE (cosi' sai quali sono
    attivamente sfruttate).
    """
    from sqlalchemy import delete
    await session.execute(delete(Vulnerability))

    # 1) raccogli i CVE che CISA KEV segnala come attivamente sfruttati
    kev_cves = set()
    for r in rows:
        if r.get("source") == "kev" and r.get("cve_id"):
            kev_cves.add(r["cve_id"].strip().upper())

    # 2) salva SOLO le fonti che generano match (vel, wporg)
    objs: list[Vulnerability] = []
    for r in rows:
        if r.get("source") not in ("vel", "wporg"):
            continue   # KEV/NVD non salvate: servono solo come flag/rumore
        cve = (r.get("cve_id", "") or "").strip()
        # una vuln VEL/wporg e' "sfruttata attivamente" se il suo CVE e' in KEV
        exploited = bool(cve and cve.upper() in kev_cves)
        v = Vulnerability(
            source=r.get("source", ""),
            cve_id=cve,
            ext_ref=r.get("ext_ref", "") or "",
            title=(r.get("title", "") or "")[:500],
            affected_slug=r.get("affected_slug", "") or "",
            affected_type=r.get("affected_type", "") or "",
            cms=r.get("cms", "") or "",
            version_fixed=r.get("version_fixed", "") or "",
            severity=("critical" if exploited else (r.get("severity", "unknown") or "unknown")),
            cvss=float(r.get("cvss", 0.0) or 0.0),
            exploited_in_wild=exploited,
            url=(r.get("url", "") or "")[:500],
            published_at=r.get("published_at"),
        )
        v._vel_status = r.get("vel_status")         # type: ignore[attr-defined]
        session.add(v)
        objs.append(v)
    await session.flush()   # per avere gli id
    return objs


def _extension_matches_vuln(ext: Extension, site_cms: str, v: Vulnerability,
                            eff_version: str | None = None) -> bool:
    """
    Decide se un'estensione installata e' colpita da una vulnerabilita'.

    IMPORTANTE: solo VEL (Joomla) e wporg (WordPress) creano match, perche' sono le
    uniche fonti con uno SLUG PRECISO dell'estensione + versione fixata. KEV e NVD usano
    nomi prodotto testuali generici ("Joomla", "Google") che generano falsi positivi a
    valanga (es. CVE di Google Chrome che matcha il plugin captcha Google). Percio' KEV/NVD
    NON creano match qui: KEV serve solo, altrove, a marcare come "sfruttata attivamente"
    una vuln gia' trovata via VEL/wporg con lo stesso CVE.

    Match SEMPRE con controllo versione: l'estensione e' vulnerabile solo se la versione
    installata e' STRETTAMENTE INFERIORE alla versione fixata. Se non c'e' versione fixata
    (LIVE VEL = nessun fix disponibile, o plugin chiuso su wporg) -> vulnerabile a
    prescindere, perche' non esiste una versione sicura a cui puntare.
    """
    # slug normalizzati per confronto
    ext_slug_j = _normalize_slug_joomla(ext.slug)
    ext_slug_w = _normalize_slug_wp(ext.slug)
    ext_name_w = _normalize_slug_wp(ext.name)

    hit = False
    if v.source == "vel":
        # Joomla: slug VEL == element normalizzato. Deve essere non vuoto e lungo abbastanza
        # da non matchare a caso (evita match su slug di 1-2 caratteri).
        if (site_cms == "joomla" and v.affected_slug
                and len(v.affected_slug) >= 4
                and v.affected_slug == ext_slug_j):
            hit = True
    elif v.source == "wporg":
        # WordPress: slug wporg == cartella plugin. Match esatto sullo slug (la cartella),
        # non sul nome (il nome e' meno affidabile).
        if (site_cms == "wp" and v.affected_slug
                and len(v.affected_slug) >= 3
                and v.affected_slug == ext_slug_w):
            hit = True
    # KEV e NVD: NESSUN match diretto (troppi falsi positivi con match testuale)
    else:
        return False

    if not hit:
        return False

    # controllo versione: vulnerabile solo se installata < fixata.
    # eff_version = versione EFFETTIVA del prodotto sul sito (la piu' alta tra tutti i record
    # che condividono lo stesso slug normalizzato). Serve perche' un pacchetto Joomla registra
    # piu' righe in #__extensions per lo stesso prodotto (component, package, plugin e a volte
    # un residuo 'file' orfano che resta a una versione vecchia). Confrontando il singolo
    # record, il residuo vecchio farebbe scattare un falso positivo anche con il prodotto
    # aggiornato (es. BaForms: component 2.4.2 ma record 'file' fermo a 1.5).
    ver = eff_version or ext.current_version
    if v.version_fixed:
        return _ver_lt(ver, v.version_fixed)
    # nessun fix noto (LIVE VEL / plugin chiuso) -> vulnerabile a prescindere
    return True


async def _rebuild_matches(session, vulns: list[Vulnerability]) -> list[dict]:
    """
    Ricostruisce i match tra vulnerabilita' e siti. Ritorna la lista dei match NUOVI
    (appena diventati vulnerabili, non ancora notificati) per l'alert Telegram.
    Preserva lo stato 'notified' dei match gia' esistenti (per non re-notificare).
    """
    # carica siti + estensioni
    sites = (await session.execute(select(Site).where(Site.enabled == True))).scalars().all()  # noqa: E712
    exts = (await session.execute(select(Extension))).scalars().all()
    ext_by_site: dict[int, list[Extension]] = {}
    for e in exts:
        ext_by_site.setdefault(e.site_id, []).append(e)

    # stato precedente dei match: chiave (site_id, vuln_id, ext_id) -> VulnMatch
    prev = (await session.execute(select(VulnMatch))).scalars().all()
    prev_map = {(m.site_id, m.vulnerability_id, m.extension_id): m for m in prev}
    seen_keys: set = set()
    new_alerts: list[dict] = []

    for site in sites:
        site_exts = ext_by_site.get(site.id, [])

        # Versione EFFETTIVA per prodotto: un pacchetto Joomla registra piu' righe in
        # #__extensions per lo stesso prodotto (component + package + plugin, a volte un
        # residuo 'file' orfano fermo a una versione vecchia). La vulnerabilita' riguarda il
        # PRODOTTO, quindi la versione da confrontare e' la piu' alta installata, non quella
        # del singolo record: senza questo, il residuo vecchio genera un falso positivo
        # (es. BaForms component 2.4.2 ma record 'file' a 1.5 -> segnalato vulnerabile).
        prod_ver: dict[str, str] = {}
        if site.cms == "joomla":
            for e in site_exts:
                k = _normalize_slug_joomla(e.slug)
                if not k:
                    continue
                cur = prod_ver.get(k)
                if cur is None or _ver_lt(cur, e.current_version or ""):
                    prod_ver[k] = e.current_version or ""

        # un solo match per (vuln, prodotto): evita 4-5 righe identiche per lo stesso pacchetto
        seen_products: set = set()

        for ext in site_exts:
            for v in vulns:
                # cms deve essere compatibile (o vuln generica)
                if v.cms and v.cms != site.cms:
                    continue
                pkey = _normalize_slug_joomla(ext.slug) if site.cms == "joomla" else _normalize_slug_wp(ext.slug)
                eff = prod_ver.get(pkey) if site.cms == "joomla" else None
                if not _extension_matches_vuln(ext, site.cms, v, eff):
                    continue
                if (v.id, pkey) in seen_products:
                    continue
                seen_products.add((v.id, pkey))

                key = (site.id, v.id, ext.id)
                seen_keys.add(key)
                existing = prev_map.get(key)
                if existing:
                    # gia' noto: assicura is_vulnerable=True, resolved_at=None. Se non e'
                    # mai stato notificato (es. sito silenziato), resta eleggibile ai cicli
                    # successivi e verra' inviato appena il silenzio viene tolto.
                    if not existing.is_vulnerable or existing.resolved_at is not None:
                        existing.is_vulnerable = True
                        existing.resolved_at = None
                    existing.site_version = ext.current_version
                    if not existing.notified:
                        new_alerts.append({
                            "site_name": site.name, "site_url": site.url,
                            "ext_name": ext.name, "ext_version": ext.current_version,
                            "notifications_silenced": bool(site.notifications_silenced),
                            "vuln": v,
                        })
                else:
                    m = VulnMatch(
                        site_id=site.id,
                        extension_id=ext.id,
                        vulnerability_id=v.id,
                        site_version=ext.current_version,
                        is_vulnerable=True,
                        notified=False,
                    )
                    session.add(m)
                    new_alerts.append({
                        "site_name": site.name,
                        "site_url": site.url,
                        "ext_name": ext.name,
                        "ext_version": ext.current_version,
                        "notifications_silenced": bool(site.notifications_silenced),
                        "vuln": v,
                    })

    # match che PRIMA erano vulnerabili e ora non compaiono piu' -> risolti
    for key, m in prev_map.items():
        if key not in seen_keys and m.is_vulnerable:
            m.is_vulnerable = False
            m.resolved_at = _now()

    await session.flush()
    return new_alerts


# ===========================================================================
# ORCHESTRAZIONE (chiamata dal job worker)
# ===========================================================================
async def refresh_and_match() -> dict:
    """
    Flusso completo: scarica tutte le fonti -> salva vulnerabilities -> ricostruisce i
    match -> ritorna i nuovi alert da notificare. NON manda Telegram qui (lo fa il worker,
    che ha telegram.py) per tenere questo modulo puro.
    """
    # recupera l'ultimo hash VEL (salvato come vuln fittizia? no: usiamo un file di stato semplice)
    last_vel_hash = _read_state("vel_hash")

    all_rows: list[dict] = []
    slugs_wp: set[str] = set()
    kw_nvd: set[str] = set()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # KEV + VEL in parallelo
        kev_rows, (vel_rows, new_vel_hash) = await asyncio.gather(
            fetch_kev(client),
            fetch_vel(client, last_vel_hash),
        )
        all_rows.extend(kev_rows)
        all_rows.extend(vel_rows)

        # costruisci l'inventario deduplicato per interrogare wporg/nvd una volta per slug
        async with SessionLocal() as s:
            exts = (await s.execute(select(Extension, Site).join(Site, Site.id == Extension.site_id))).all()
        for ext, site in exts:
            if site.cms == "wp" and ext.type == "plugin" and ext.slug:
                slugs_wp.add(ext.slug.strip().lower())
            # keyword NVD: nome estensione (piu' significativo dello slug per la ricerca testuale)
            nm = (ext.name or "").strip().lower()
            if nm and len(nm) >= 5:
                kw_nvd.add(nm)

        # wporg per gli slug WP distinti
        if slugs_wp:
            all_rows.extend(await fetch_wporg_for_slugs(client, sorted(slugs_wp)))

        # NVD: solo se abilitato (lento). Limita a un tetto di keyword per non impiegarci troppo.
        if getattr(settings, "SECURITY_USE_NVD", True):
            kw_list = sorted(kw_nvd)[: getattr(settings, "SECURITY_NVD_MAX_KEYWORDS", 40)]
            all_rows.extend(await fetch_nvd_for_keywords(client, kw_list))

    # salva vuln + match in transazione
    async with SessionLocal() as s:
        vulns = await _save_vulnerabilities(s, all_rows)
        new_alerts = await _rebuild_matches(s, vulns)
        await s.commit()

    if new_vel_hash:
        _write_state("vel_hash", new_vel_hash)

    return {
        "vulns": len(all_rows),
        "new_alerts": new_alerts,
        "sources": {
            "kev": sum(1 for r in all_rows if r["source"] == "kev"),
            "vel": sum(1 for r in all_rows if r["source"] == "vel"),
            "wporg": sum(1 for r in all_rows if r["source"] == "wporg"),
            "nvd": sum(1 for r in all_rows if r["source"] == "nvd"),
        },
    }


# ---------------------------------------------------------------------------
# stato leggero su file (per l'hash VEL). Evita una tabella dedicata.
# ---------------------------------------------------------------------------
import os
_STATE_DIR = getattr(settings, "SECURITY_STATE_DIR", "/tmp/panopticon-sec")


def _read_state(key: str) -> str | None:
    try:
        with open(os.path.join(_STATE_DIR, key), "r") as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return None


def _write_state(key: str, val: str) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(os.path.join(_STATE_DIR, key), "w") as f:
            f.write(val)
    except Exception:  # noqa: BLE001
        pass
