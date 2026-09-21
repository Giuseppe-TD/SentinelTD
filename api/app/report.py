"""
Report mensile di Sentinel TD.

Raccoglie i dati del mese dal rollup (update_monthly) piu' lo stato attuale di
sicurezza e scadenze, li passa a un template HTML EDITABILE dal pannello e ne
produce un PDF intestato con il logo.

Il PDF e' generato con WeasyPrint se disponibile; se manca (immagine senza le
librerie di sistema) il report viene comunque prodotto e allegato in HTML, cosi'
la funzione degrada invece di rompersi.
"""
import base64
import json
import logging
import mimetypes
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, BaseLoader, TemplateError
from sqlalchemy import select, func, text

from .db import SessionLocal
from .models import AppSetting, UpdateMonthly, Site, SiteExpiry
from .config import settings

from .i18n import t, template as localize_template

log = logging.getLogger("report")

CONFIG_KEY = "report:config"
TEMPLATE_KEY = "report:template"
LAST_SENT_KEY = "report:last_sent"

MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

DEFAULTS = {
    "enabled": True,
    "send_day": 1,          # giorno del mese in cui inviare (1-28)
    "send_hour": 8,         # ora locale
    "recipients": "",       # vuoto = usa REPORT_TO del .env; piu' indirizzi separati da virgola
    "company": "Tastiere Digitali",
    "title": "Report manutenzione siti web",
    "intro": "Di seguito il riepilogo delle attività di manutenzione e aggiornamento svolte nel periodo.",
    "footer": "Report generato automaticamente da Sentinel TD.",
    "show_summary": True,        # KPI del mese
    "show_sites": True,          # dettaglio per sito (quante volte, quali plugin)
    "show_top": True,            # classifica estensioni piu' aggiornate
    "show_failed": True,         # elenco update falliti
    "show_security": True,       # stato vulnerabilita'
    "show_expiries": True,       # scadenze domini/licenze in arrivo
    "show_compare": True,        # confronto col mese precedente + andamento
    "expiry_horizon_days": 60,
    # Report da produrre/inviare ogni mese. Ogni voce: {key, enabled}
    # key = "__all__" (tutti i siti) oppure il nome esatto di una cartella/tag.
    # Tutti i report vengono inviati all'unico indirizzo configurato in "recipients".
    "scopes": [{"key": "__all__", "enabled": True}],
}

GLOBAL_KEY = "__all__"

# ---------------------------------------------------------------- template
DEFAULT_TEMPLATE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<style>
  @page { size: A4; margin: 18mm 15mm 16mm; @bottom-center { content: "Pagina " counter(page) " di " counter(pages); font: 9px Helvetica, sans-serif; color: #999; } }
  * { box-sizing: border-box; }
  body { font: 11px/1.5 Helvetica, Arial, sans-serif; color: #24292f; margin: 0; }
  .head { display: flex; align-items: center; border-bottom: 2px solid #1f6feb; padding-bottom: 12px; margin-bottom: 18px; }
  .head img { max-height: 46px; max-width: 200px; }
  .head .t { margin-left: auto; text-align: right; }
  .head h1 { font-size: 17px; margin: 0 0 2px; }
  .head .p { color: #6a737d; font-size: 11px; }
  h2 { font-size: 13px; margin: 20px 0 8px; color: #1f2933; border-left: 3px solid #1f6feb; padding-left: 8px; }
  .intro { color: #444; margin-bottom: 14px; }
  .kpi { display: flex; gap: 10px; margin-bottom: 6px; }
  .kpi div { flex: 1; border: 1px solid #e1e4e8; border-radius: 6px; padding: 10px 12px; }
  .kpi .v { font-size: 20px; font-weight: 700; color: #1f6feb; }
  .kpi .k { font-size: 9.5px; color: #6a737d; text-transform: uppercase; letter-spacing: .04em; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 10px; }
  th { text-align: left; font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em; color: #6a737d; border-bottom: 1px solid #d0d7de; padding: 5px 6px; }
  td { padding: 5px 6px; border-bottom: 1px solid #eef1f4; vertical-align: top; }
  .site { page-break-inside: avoid; margin-bottom: 14px; }
  .site h3 { font-size: 12px; margin: 0 0 1px; }
  .site .u { color: #6a737d; font-size: 10px; margin-bottom: 5px; }
  .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .mut { color: #6a737d; }
  .ko { color: #b00020; }
  .up { color: #1a7f4b; } .dn { color: #b00020; }
  .trend { display: flex; align-items: flex-end; gap: 8px; height: 76px; margin: 10px 0 4px; }
  .trend .tb { flex: 1; text-align: center; }
  .trend .tv { font-size: 9px; color: #6a737d; margin-bottom: 2px; }
  .trend .tbar { background: #cfe0ff; border-radius: 3px 3px 0 0; }
  .trend .tbar.cur { background: #1f6feb; }
  .trend .tl { font-size: 9px; color: #6a737d; margin-top: 3px; text-transform: capitalize; }
  .foot { margin-top: 22px; padding-top: 10px; border-top: 1px solid #e1e4e8; color: #8a949e; font-size: 9.5px; }
</style></head><body>

<div class="head">
  {% if logo %}<img src="{{ logo }}">{% endif %}
  <div class="t"><h1>{{ cfg.title }}</h1><div class="p">{{ period_label }}{% if cfg.company %} &middot; {{ cfg.company }}{% endif %}</div>
  {% if not is_global %}<div class="p" style="font-weight:600;color:#1f6feb">{{ scope_label }}</div>{% endif %}</div>
</div>

{% if cfg.intro %}<div class="intro">{{ cfg.intro }}</div>{% endif %}

{% if cfg.show_summary %}
<h2>Riepilogo del periodo</h2>
<div class="kpi">
  <div><div class="v">{{ total_updates }}</div><div class="k">Aggiornamenti applicati</div></div>
  <div><div class="v">{{ sites_touched }}</div><div class="k">Siti aggiornati</div></div>
  <div><div class="v">{{ distinct_items }}</div><div class="k">Componenti diversi</div></div>
  <div><div class="v">{{ total_failed }}</div><div class="k">Non riusciti</div></div>
</div>
<div class="mut">Siti in monitoraggio: {{ sites_total }} ({{ wp_count }} WordPress, {{ joomla_count }} Joomla).</div>
{% endif %}

{% if cfg.show_compare %}
<h2>Confronto con {{ prev.label }}</h2>
<table>
  <thead><tr><th>Indicatore</th><th class="n">{{ period_label }}</th><th class="n">{{ prev.label }}</th><th class="n">Variazione</th></tr></thead>
  <tbody>
    <tr><td>Aggiornamenti applicati</td><td class="n">{{ total_updates }}</td><td class="n">{{ prev.updates }}</td><td class="n {% if delta.updates.up %}up{% elif delta.updates.down %}dn{% endif %}">{{ delta.updates.arrow }} {{ delta.updates.text }}</td></tr>
    <tr><td>Siti aggiornati</td><td class="n">{{ sites_touched }}</td><td class="n">{{ prev.sites }}</td><td class="n">{{ delta.sites.arrow }} {{ delta.sites.text }}</td></tr>
    <tr><td>Componenti diversi</td><td class="n">{{ distinct_items }}</td><td class="n">{{ prev.components }}</td><td class="n">{{ delta.components.arrow }} {{ delta.components.text }}</td></tr>
    <tr><td>Non riusciti</td><td class="n">{{ total_failed }}</td><td class="n">{{ prev.failed }}</td><td class="n {% if delta.failed.up %}dn{% elif delta.failed.down %}up{% endif %}">{{ delta.failed.arrow }} {{ delta.failed.text }}</td></tr>
  </tbody>
</table>
{% if trend %}
<div class="trend">
  {% for t in trend %}<div class="tb"><div class="tv">{{ t.updates }}</div><div class="tbar{% if t.current %} cur{% endif %}" style="height:{{ t.h }}px"></div><div class="tl">{{ t.label.split(' ')[0][:3] }}</div></div>{% endfor %}
</div>
<div class="mut">Aggiornamenti applicati negli ultimi 6 mesi.</div>
{% endif %}
{% endif %}

{% if cfg.show_sites and sites %}
<h2>Dettaglio per sito</h2>
{% for s in sites %}
<div class="site">
  <h3>{{ s.name }}</h3>
  <div class="u">{{ s.url }} &middot; {{ s.cms_label }} &middot; {{ s.total }} aggiornamenti su {{ s.components|length }} componenti</div>
  <table>
    <thead><tr><th>Componente</th><th>Tipo</th><th class="n">Volte</th><th>Ultima versione</th></tr></thead>
    <tbody>
    {% for i in s.components %}
      <tr><td>{{ i.name }}</td><td class="mut">{{ i.type_label }}</td><td class="n">{{ i.count }}</td><td class="mut">{{ i.last_version or '—' }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endfor %}
{% endif %}

{% if cfg.show_top and top_items %}
<h2>Componenti più aggiornati</h2>
<table>
  <thead><tr><th>Componente</th><th class="n">Aggiornamenti</th><th class="n">Siti</th></tr></thead>
  <tbody>{% for t in top_items %}<tr><td>{{ t.name }}</td><td class="n">{{ t.count }}</td><td class="n">{{ t.sites }}</td></tr>{% endfor %}</tbody>
</table>
{% endif %}

{% if cfg.show_failed and failed_items %}
<h2>Aggiornamenti non riusciti</h2>
<table>
  <thead><tr><th>Sito</th><th>Componente</th><th class="n">Tentativi</th></tr></thead>
  <tbody>{% for f in failed_items %}<tr><td>{{ f.site }}</td><td>{{ f.name }}</td><td class="n ko">{{ f.count }}</td></tr>{% endfor %}</tbody>
</table>
<div class="mut">Gli aggiornamenti non riusciti vengono ritentati automaticamente nei cicli successivi.</div>
{% endif %}

{% if cfg.show_security %}
<h2>Sicurezza</h2>
{% if security.total %}
<div class="mut">Vulnerabilità note attualmente rilevate sulle estensioni installate:</div>
<table>
  <thead><tr><th>Gravità</th><th class="n">Occorrenze</th></tr></thead>
  <tbody>
    <tr><td>Critiche</td><td class="n">{{ security.critical }}</td></tr>
    <tr><td>Alte</td><td class="n">{{ security.high }}</td></tr>
    <tr><td>Medie</td><td class="n">{{ security.medium }}</td></tr>
    <tr><td>Basse</td><td class="n">{{ security.low }}</td></tr>
  </tbody>
</table>
{% else %}
<div>Nessuna vulnerabilità nota attiva sui siti monitorati.</div>
{% endif %}
{% endif %}

{% if cfg.show_expiries and (domains or licenses) %}
<h2>Scadenze nei prossimi {{ cfg.expiry_horizon_days }} giorni</h2>
{% if domains %}
<table>
  <thead><tr><th>Dominio</th><th>Scadenza</th><th class="n">Giorni</th></tr></thead>
  <tbody>{% for d in domains %}<tr><td>{{ d.name }}</td><td>{{ d.date }}</td><td class="n">{{ d.days }}</td></tr>{% endfor %}</tbody>
</table>
{% endif %}
{% if licenses %}
<table>
  <thead><tr><th>Licenza / componente</th><th>Fornitore</th><th>Scadenza</th><th class="n">Giorni</th></tr></thead>
  <tbody>{% for l in licenses %}<tr><td>{{ l.name }}</td><td class="mut">{{ l.provider or '—' }}</td><td>{{ l.date }}</td><td class="n">{{ l.days }}</td></tr>{% endfor %}</tbody>
</table>
{% endif %}
{% endif %}

<div class="foot">{{ cfg.footer }} &middot; generato il {{ generated_at }}{% if app_version %} &middot; Sentinel TD v{{ app_version }}{% endif %}</div>
</body></html>
"""

_env = Environment(loader=BaseLoader(), autoescape=True)


# ---------------------------------------------------------------- config
def normalize(data: dict | None) -> dict:
    src = data or {}
    out = deepcopy(DEFAULTS)
    for key in ("title", "intro", "footer"):
        out[key] = t(out[key])
    for k in ("company", "title", "intro", "footer", "recipients"):
        if k in src and isinstance(src[k], str):
            out[k] = src[k].strip()[:2000]
    for k in ("enabled", "show_summary", "show_sites", "show_top", "show_failed", "show_security", "show_expiries", "show_compare"):
        if k in src:
            out[k] = bool(src[k])
    try:
        out["send_day"] = max(1, min(28, int(src.get("send_day", out["send_day"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["send_hour"] = max(0, min(23, int(src.get("send_hour", out["send_hour"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["expiry_horizon_days"] = max(7, min(365, int(src.get("expiry_horizon_days", out["expiry_horizon_days"]))))
    except (TypeError, ValueError):
        pass

    # scopes: uno per cartella + quello globale. Migra la vecchia "only_folder".
    raw = src.get("scopes")
    scopes: list[dict] = []
    if isinstance(raw, list):
        for item in raw[:60]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()[:190]
            if not key:
                continue
            if any(x["key"].lower() == key.lower() for x in scopes):
                continue
            scopes.append({"key": key, "enabled": bool(item.get("enabled", True))})
    elif src.get("only_folder"):
        scopes.append({"key": str(src["only_folder"]).strip(), "enabled": True})
    if not scopes:
        scopes = [{"key": GLOBAL_KEY, "enabled": True}]
    out["scopes"] = scopes
    return out


def scope_label(key: str) -> str:
    return "Tutti i siti" if (not key or key == GLOBAL_KEY) else key


def scope_slug(key: str) -> str:
    if not key or key == GLOBAL_KEY:
        return "globale"
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    return s or "cartella"


def report_recipients(cfg: dict) -> str:
    """Destinatario dei report: unico, dalle impostazioni (vuoto = REPORT_TO del .env)."""
    return (cfg.get("recipients") or "").strip()


async def get_config() -> dict:
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, CONFIG_KEY)
            if row and row.value:
                return normalize(json.loads(row.value))
    except Exception:  # noqa: BLE001
        pass
    return normalize({})


async def save_config(data: dict) -> dict:
    clean = normalize(data)
    async with SessionLocal() as s:
        row = await s.get(AppSetting, CONFIG_KEY)
        if row:
            row.value = json.dumps(clean)
        else:
            s.add(AppSetting(key=CONFIG_KEY, value=json.dumps(clean)))
        await s.commit()
    return clean


async def get_template() -> str:
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, TEMPLATE_KEY)
            if row and row.value.strip():
                return row.value
    except Exception:  # noqa: BLE001
        pass
    return localize_template(DEFAULT_TEMPLATE)


async def save_template(html: str) -> None:
    async with SessionLocal() as s:
        row = await s.get(AppSetting, TEMPLATE_KEY)
        if row:
            row.value = html
        else:
            s.add(AppSetting(key=TEMPLATE_KEY, value=html))
        await s.commit()


# ---------------------------------------------------------------- helpers
def period_label(period: str) -> str:
    try:
        y, m = period.split("-")
        return f"{t(MESI[int(m)]).capitalize()} {y}"
    except Exception:  # noqa: BLE001
        return period


def prev_period(ref: datetime | None = None) -> str:
    d = (ref or datetime.now()).replace(day=1) - timedelta(days=1)
    return d.strftime("%Y-%m")


def current_period() -> str:
    return datetime.now().strftime("%Y-%m")


_TYPE_LABELS = {"plugin": "Plugin", "theme": "Tema", "core": "Core", "translation": "Traduzioni",
                "component": "Componente", "module": "Modulo", "package": "Pacchetto", "library": "Libreria",
                "file": "File", "other": "Altro"}



def shift_period(period: str, months: int) -> str:
    """Periodo spostato di N mesi (negativo = indietro)."""
    y, m = int(period[:4]), int(period[5:7])
    t = (y * 12 + (m - 1)) + months
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


async def month_stats(s, period: str, allowed: set[int] | None) -> dict:
    """Totali di un mese per il perimetro indicato (None = tutti i siti)."""
    rows = (await s.execute(select(UpdateMonthly).where(UpdateMonthly.period == period))).scalars().all()
    if allowed is not None:
        rows = [x for x in rows if x.site_id in allowed]
    ok = sum(x.ok_count for x in rows)
    return {
        "period": period, "label": period_label(period),
        "updates": ok,
        "failed": sum(x.fail_count for x in rows),
        "sites": len({x.site_id for x in rows if x.ok_count > 0}),
        "components": len({(x.ext_name or x.slug).lower() for x in rows if x.ok_count > 0}),
    }


def _delta(cur: int, prev: int) -> dict:
    """Variazione assoluta e percentuale, con freccia gia' pronta per il template."""
    diff = cur - prev
    pct = None if prev == 0 else round(diff * 100 / prev)
    return {"diff": diff, "pct": pct, "up": diff > 0, "down": diff < 0,
            "arrow": "▲" if diff > 0 else ("▼" if diff < 0 else "="),
            "text": ("+" if diff > 0 else "") + str(diff) + ("" if pct is None else f" ({'+' if diff > 0 else ''}{pct}%)")}

async def _logo_data_uri() -> str:
    """Logo come data URI: il PDF deve essere autonomo, senza chiamate HTTP."""
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, "brand:logo_path")
            p = Path(row.value) if row and row.value else Path("static/logo.png")
        if not p.is_file():
            p = Path("static/logo.png")
        if not p.is_file():
            return ""
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- dati
async def gather(period: str, cfg: dict | None = None, scope: str = "") -> dict:
    cfg = cfg or await get_config()
    horizon = int(cfg.get("expiry_horizon_days", 60))
    only = "" if (not scope or scope == GLOBAL_KEY) else scope.strip().lower()

    async with SessionLocal() as s:
        sites_rows = (await s.execute(select(Site))).scalars().all()
        by_id = {x.id: x for x in sites_rows}
        if only:
            allowed = {x.id for x in sites_rows
                       if any(t.strip().lower() == only or t.strip().lower().startswith(only + "/")
                              for t in (x.tags or "").split(","))}
        else:
            allowed = {x.id for x in sites_rows}

        rows = (await s.execute(
            select(UpdateMonthly).where(UpdateMonthly.period == period).order_by(UpdateMonthly.site_name, UpdateMonthly.ext_name)
        )).scalars().all()
        rows = [r for r in rows if r.site_id in allowed or not allowed]

        # --- per sito ---
        sites: dict[int, dict] = {}
        for r in rows:
            if r.ok_count <= 0:
                continue
            site = by_id.get(r.site_id)
            entry = sites.setdefault(r.site_id, {
                "name": r.site_name or (site.name if site else f"Sito {r.site_id}"),
                "url": (site.url if site else "").replace("https://", "").replace("http://", ""),
                "cms_label": "WordPress" if (r.cms or (site.cms if site else "")) in ("wp", "wordpress") else "Joomla",
                "components": [], "total": 0,
            })
            entry["components"].append({
                "name": r.ext_name or r.slug, "type_label": _TYPE_LABELS.get(r.ext_type, (r.ext_type or "").capitalize() or "Altro"),
                "count": r.ok_count, "last_version": r.last_version,
            })
            entry["total"] += r.ok_count
        for e in sites.values():
            e["components"].sort(key=lambda i: (-i["count"], i["name"].lower()))
        site_list = sorted(sites.values(), key=lambda e: (-e["total"], e["name"].lower()))

        # --- classifica componenti ---
        agg: dict[str, dict] = {}
        for r in rows:
            if r.ok_count <= 0:
                continue
            key = (r.ext_name or r.slug).lower()
            a = agg.setdefault(key, {"name": r.ext_name or r.slug, "count": 0, "sites": 0})
            a["count"] += r.ok_count
            a["sites"] += 1
        top_items = sorted(agg.values(), key=lambda a: (-a["count"], a["name"].lower()))[:15]

        # --- falliti ---
        failed_items = [{"site": r.site_name, "name": r.ext_name or r.slug, "count": r.fail_count}
                        for r in rows if r.fail_count > 0]
        failed_items.sort(key=lambda f: -f["count"])

        # --- sicurezza (stato attuale) ---
        security = {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
        try:
            res = (await s.execute(text("""
                SELECT v.severity, count(*) FROM vuln_matches m
                JOIN vulnerabilities v ON v.id = m.vulnerability_id
                WHERE m.is_vulnerable = true AND m.resolved_at IS NULL
                GROUP BY v.severity
            """))).all()
            for sev, n in res:
                security["total"] += n
                if (sev or "").lower() in security:
                    security[(sev or "").lower()] += n
        except Exception:  # noqa: BLE001
            pass

        # --- scadenze ---
        now = datetime.now(timezone.utc)
        limit = now + timedelta(days=horizon)
        domains, seen = [], set()
        for x in sites_rows:
            if only and x.id not in allowed:
                continue
            d = getattr(x, "domain_expires_at", None)
            nm = getattr(x, "domain_name", "") or ""
            if not d or not nm or nm in seen:
                continue
            if d <= limit:
                seen.add(nm)
                domains.append({"name": nm, "date": d.strftime("%d/%m/%Y"), "days": (d - now).days})
        domains.sort(key=lambda d: d["days"])

        lic_rows = (await s.execute(
            select(SiteExpiry).where(SiteExpiry.expires_at <= limit).order_by(SiteExpiry.expires_at)
        )).scalars().all()
        licenses = [{"name": x.name, "provider": x.provider, "date": x.expires_at.strftime("%d/%m/%Y"),
                     "days": (x.expires_at - now).days} for x in lic_rows]

        # --- confronto col mese precedente + andamento ultimi 6 mesi ---
        perimeter = allowed if only else None
        prev = await month_stats(s, shift_period(period, -1), perimeter)
        trend = []
        for i in range(5, -1, -1):
            trend.append(await month_stats(s, shift_period(period, -i), perimeter))
        top_trend = max([t["updates"] for t in trend] + [1])
        for t in trend:
            t["h"] = max(2, round(t["updates"] * 46 / top_trend))   # altezza barra nel PDF (px)
            t["current"] = t["period"] == period

    total_updates = sum(e["total"] for e in site_list)
    cur_stats = {"updates": total_updates, "failed": sum(f["count"] for f in failed_items),
                 "sites": len(site_list), "components": len(agg)}
    delta = {k: _delta(cur_stats[k], prev[k]) for k in ("updates", "failed", "sites", "components")}
    from .version import __version__ as _ver
    return {
        "cfg": cfg,
        "app_version": _ver,
        "period": period,
        "period_label": period_label(period),
        "scope": scope or GLOBAL_KEY,
        "scope_label": scope_label(scope),
        "is_global": not only,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_updates": total_updates,
        "total_failed": sum(f["count"] for f in failed_items),
        "sites_touched": len(site_list),
        "distinct_items": len(agg),
        "sites_total": len([x for x in sites_rows if x.id in allowed]),
        "wp_count": sum(1 for x in sites_rows if x.id in allowed and x.cms == "wp"),
        "joomla_count": sum(1 for x in sites_rows if x.id in allowed and x.cms != "wp"),
        "sites": site_list,
        "top_items": top_items,
        "failed_items": failed_items[:25],
        "security": security,
        "domains": domains,
        "licenses": licenses,
        "prev": prev,
        "delta": delta,
        "trend": trend,
    }


# ---------------------------------------------------------------- render
async def render_html(period: str, template: str | None = None, cfg: dict | None = None, scope: str = "") -> str:
    cfg = cfg or await get_config()
    ctx = await gather(period, cfg, scope)
    ctx["logo"] = await _logo_data_uri()
    tpl = template if template is not None else await get_template()
    try:
        return _env.from_string(tpl).render(**ctx)
    except TemplateError as ex:
        log.warning("template report non valido (%s): uso il default", ex)
        return _env.from_string(localize_template(DEFAULT_TEMPLATE)).render(**ctx)


def html_to_pdf(html: str) -> bytes | None:
    """PDF con WeasyPrint. None se la libreria non e' disponibile nell'immagine."""
    try:
        from weasyprint import HTML  # import locale: l'app parte anche senza
        return HTML(string=html, base_url=".").write_pdf()
    except Exception as ex:  # noqa: BLE001
        log.warning("PDF non generato (%s): verra' allegato l'HTML", ex)
        return None


async def build(period: str, scope: str = "") -> tuple[str, bytes | None, str]:
    """Ritorna (html, pdf_bytes|None, filename) per il periodo e la cartella indicati."""
    cfg = await get_config()
    html = await render_html(period, cfg=cfg, scope=scope)
    pdf = html_to_pdf(html)
    base = f"report-{period}-{scope_slug(scope)}"
    return html, pdf, (f"{base}.pdf" if pdf else f"{base}.html")
