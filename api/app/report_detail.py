"""
Report dettagliato degli aggiornamenti, su richiesta.

Per uno, alcuni o tutti i siti, su un intervallo di mesi a scelta: per ogni
componente quante volte e' stato aggiornato e da quale versione a quale, e
(opzionale) la cronologia di ogni singolo aggiornamento con data ed esito.

Fonti dei dati:
  - update_monthly  -> conteggi, versione di partenza e di arrivo (conservato per sempre)
  - update_history  -> il dettaglio del singolo aggiornamento (conservato N giorni,
                       configurabile in Impostazioni, default 400)
"""
import csv
import io
import re
from datetime import datetime

from jinja2 import Environment, BaseLoader
from sqlalchemy import select, func

from .db import SessionLocal
from .models import Site, UpdateMonthly, UpdateHistory
from . import report as rep
from .i18n import t, template as localize_template

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_TYPE_LABELS = {
    "plugin": "Plugin", "theme": "Tema", "core": "Core", "translation": "Traduzioni",
    "language": "Lingua", "component": "Componente", "module": "Modulo", "package": "Pacchetto",
    "library": "Libreria", "file": "File", "template": "Template", "other": "Altro",
}


def valid_period(p: str) -> bool:
    return bool(p and _PERIOD_RE.match(p))


def _bounds(p_from: str, p_to: str) -> tuple[datetime, datetime]:
    """Inizio del primo mese e inizio del mese successivo all'ultimo, in ora locale."""
    y, m = int(p_from[:4]), int(p_from[5:7])
    start = datetime(y, m, 1).astimezone()
    y2, m2 = int(p_to[:4]), int(p_to[5:7])
    y2, m2 = (y2 + 1, 1) if m2 == 12 else (y2, m2 + 1)
    end = datetime(y2, m2, 1).astimezone()
    return start, end


def range_label(p_from: str, p_to: str) -> str:
    a, b = rep.period_label(p_from), rep.period_label(p_to)
    return a if p_from == p_to else f"{a} – {b}"


async def gather(p_from: str, p_to: str, site_ids: list[int] | None,
                 include_history: bool = True, include_failed: bool = True) -> dict:
    async with SessionLocal() as s:
        sites = (await s.execute(select(Site))).scalars().all()
        wanted = set(site_ids) if site_ids else {x.id for x in sites}
        chosen = sorted((x for x in sites if x.id in wanted), key=lambda x: (x.name or "").lower())

        rows = (await s.execute(
            select(UpdateMonthly)
            .where(UpdateMonthly.period >= p_from, UpdateMonthly.period <= p_to)
            .order_by(UpdateMonthly.period)
        )).scalars().all()
        rows = [r for r in rows if r.site_id in wanted]

        hist, oldest = [], None
        if include_history:
            start, end = _bounds(p_from, p_to)
            q = (select(UpdateHistory)
                 .where(UpdateHistory.created_at >= start, UpdateHistory.created_at < end,
                        UpdateHistory.site_id.in_(wanted))
                 .order_by(UpdateHistory.site_id, UpdateHistory.created_at))
            hist = (await s.execute(q)).scalars().all()
            oldest = (await s.execute(select(func.min(UpdateHistory.created_at)))).scalar()

    # --- aggregazione per sito e componente (i mesi arrivano gia' in ordine) ---
    per_site: dict[int, dict[str, dict]] = {}
    for r in rows:
        comps = per_site.setdefault(r.site_id, {})
        key = f"{r.ext_type}|{r.slug}"
        c = comps.setdefault(key, {
            "name": r.ext_name or r.slug, "type": t(_TYPE_LABELS.get(r.ext_type, (r.ext_type or "").capitalize())),
            "slug": r.slug, "count": 0, "failed": 0, "from": "", "to": "", "months": [],
        })
        c["count"] += r.ok_count or 0
        c["failed"] += r.fail_count or 0
        if not c["from"] and r.first_version:
            c["from"] = r.first_version            # primo mese con dato: versione di partenza
        if r.ok_count and r.last_version:
            c["to"] = r.last_version               # ultimo mese: versione di arrivo
        if r.ok_count:
            c["months"].append(rep.period_label(r.period))
        if r.ext_name:
            c["name"] = r.ext_name

    hist_by_site: dict[int, list] = {}
    for h in hist:
        if not include_failed and not h.ok:
            continue
        hist_by_site.setdefault(h.site_id, []).append({
            "date": h.created_at.astimezone().strftime("%d/%m/%Y %H:%M") if h.created_at else "",
            "name": h.ext_name or h.slug,
            "type": t(_TYPE_LABELS.get(h.ext_type, (h.ext_type or "").capitalize())),
            "from": h.from_version or "", "to": h.to_version or "",
            "ok": bool(h.ok), "error": (h.error or "")[:200],
        })

    out_sites, idle = [], []
    for site in chosen:
        comps = list(per_site.get(site.id, {}).values())
        if not include_failed:
            comps = [c for c in comps if c["count"] > 0]
        comps.sort(key=lambda c: (-c["count"], c["name"].lower()))
        history = hist_by_site.get(site.id, [])
        if not comps and not history:
            idle.append(site.name)
            continue
        out_sites.append({
            "id": site.id, "name": site.name,
            "url": (site.url or "").replace("https://", "").replace("http://", ""),
            "cms_label": "WordPress" if site.cms == "wp" else "Joomla",
            "tags": site.tags or "",
            "total": sum(c["count"] for c in comps),
            "failed": sum(c["failed"] for c in comps),
            "components": comps,
            "history": history,
        })

    distinct = {(c["type"], c["slug"]) for sdata in out_sites for c in sdata["components"] if c["count"]}
    history_from = None
    if include_history and oldest:
        start, _ = _bounds(p_from, p_to)
        if oldest.astimezone() > start:
            history_from = oldest.astimezone().strftime("%d/%m/%Y")
    return {
        "p_from": p_from, "p_to": p_to, "range_label": range_label(p_from, p_to),
        "sites": out_sites, "idle": idle,
        "sites_total": len(chosen),
        "total_updates": sum(x["total"] for x in out_sites),
        "total_failed": sum(x["failed"] for x in out_sites),
        "sites_touched": sum(1 for x in out_sites if x["total"] > 0),
        "distinct": len(distinct),
        "include_history": include_history,
        "include_failed": include_failed,
        "history_from": history_from,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


# ---------------------------------------------------------------- PDF
DETAIL_TEMPLATE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<style>
  @page { size: A4; margin: 16mm 13mm 15mm; @bottom-center { content: "Pagina " counter(page) " di " counter(pages); font: 9px Helvetica, sans-serif; color: #999; } }
  * { box-sizing: border-box; }
  body { font: 10.5px/1.45 Helvetica, Arial, sans-serif; color: #24292f; margin: 0; }
  .head { display: flex; align-items: center; border-bottom: 2px solid #1f6feb; padding-bottom: 12px; margin-bottom: 16px; }
  .head img { max-height: 44px; max-width: 190px; }
  .head .t { margin-left: auto; text-align: right; }
  .head h1 { font-size: 16px; margin: 0 0 2px; }
  .head .p { color: #6a737d; font-size: 10.5px; }
  .kpi { display: flex; gap: 9px; margin-bottom: 8px; }
  .kpi div { flex: 1; border: 1px solid #e1e4e8; border-radius: 6px; padding: 8px 10px; }
  .kpi .v { font-size: 18px; font-weight: 700; color: #1f6feb; }
  .kpi .k { font-size: 9px; color: #6a737d; text-transform: uppercase; letter-spacing: .04em; }
  .note { color: #6a737d; font-size: 9.5px; margin: 4px 0 12px; }
  .site { margin: 18px 0 8px; }
  .site h2 { font-size: 13px; margin: 0; padding: 7px 10px; background: #f3f6fb; border-left: 3px solid #1f6feb; page-break-after: avoid; }
  .site .u { color: #6a737d; font-size: 9.5px; padding: 3px 10px 6px; page-break-after: avoid; }
  h3 { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: #6a737d; margin: 10px 0 4px; page-break-after: avoid; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 6px; }
  thead { display: table-header-group; }
  th { text-align: left; font-size: 8.5px; text-transform: uppercase; letter-spacing: .04em; color: #6a737d; border-bottom: 1px solid #d0d7de; padding: 4px 6px; }
  td { padding: 4px 6px; border-bottom: 1px solid #eef1f4; vertical-align: top; }
  tr { page-break-inside: avoid; }
  .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .mono { font-family: "DejaVu Sans Mono", Menlo, monospace; font-size: 9.5px; white-space: nowrap; }
  .mut { color: #6a737d; }
  .ko { color: #b00020; }
  .ok { color: #1a7f4b; }
  .arrow { color: #8a949e; padding: 0 3px; }
  .idle { margin-top: 18px; padding: 8px 10px; border: 1px solid #e1e4e8; border-radius: 6px; color: #6a737d; font-size: 9.5px; }
  .foot { margin-top: 18px; padding-top: 8px; border-top: 1px solid #e1e4e8; color: #8a949e; font-size: 9px; }
</style></head><body>

<div class="head">
  {% if logo %}<img src="{{ logo }}">{% endif %}
  <div class="t"><h1>Report dettagliato aggiornamenti</h1>
    <div class="p">{{ range_label }}{% if company %} &middot; {{ company }}{% endif %}</div>
    <div class="p" style="font-weight:600;color:#1f6feb">{{ scope_label }}</div>
  </div>
</div>

<div class="kpi">
  <div><div class="v">{{ total_updates }}</div><div class="k">Aggiornamenti</div></div>
  <div><div class="v">{{ sites_touched }}</div><div class="k">Siti aggiornati</div></div>
  <div><div class="v">{{ distinct }}</div><div class="k">Componenti diversi</div></div>
  <div><div class="v">{{ total_failed }}</div><div class="k">Non riusciti</div></div>
</div>
{% if include_history and history_from %}<div class="note"><span>La cronologia dei singoli aggiornamenti è disponibile dal</span> <b>{{ history_from }}</b>. <span>Per il periodo precedente sono riportati i totali mensili.</span></div>{% endif %}

{% for s in sites %}
<div class="site">
  <h2>{{ s.name }}</h2>
  <div class="u">{{ s.url }} &middot; {{ s.cms_label }} &middot; {{ s.total }} aggiornamenti su {{ s.components|length }} componenti{% if s.failed %} &middot; <span class="ko">{{ s.failed }} non riusciti</span>{% endif %}</div>
  {% if s.components %}
  <h3>Riepilogo per componente</h3>
  <table>
    <thead><tr><th>Componente</th><th>Tipo</th><th class="n">Volte</th><th>Versioni (da → a)</th>{% if include_failed %}<th class="n">Falliti</th>{% endif %}</tr></thead>
    <tbody>
    {% for c in s.components %}
      <tr><td>{{ c.name }}</td><td class="mut">{{ c.type }}</td><td class="n">{{ c.count }}</td>
          <td class="mono">{{ c['from'] or '—' }}<span class="arrow">&rarr;</span>{{ c.to or '—' }}</td>
          {% if include_failed %}<td class="n {% if c.failed %}ko{% endif %}">{{ c.failed or '' }}</td>{% endif %}</tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% if include_history and s.history %}
  <h3>Cronologia degli aggiornamenti</h3>
  <table>
    <thead><tr><th>Data</th><th>Componente</th><th>Versioni (da → a)</th><th>Esito</th></tr></thead>
    <tbody>
    {% for h in s.history %}
      <tr><td class="mono">{{ h.date }}</td><td>{{ h.name }}</td>
          <td class="mono">{{ h['from'] or '—' }}<span class="arrow">&rarr;</span>{{ h.to or '—' }}</td>
          <td>{% if h.ok %}<span class="ok">riuscito</span>{% else %}<span class="ko">non riuscito</span>{% if h.error %}<div class="mut">{{ h.error }}</div>{% endif %}{% endif %}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
</div>
{% endfor %}

{% if not sites %}<div class="idle">Nessun aggiornamento registrato nel periodo per i siti scelti.</div>{% endif %}
{% if idle %}<div class="idle"><b><span>Siti senza aggiornamenti nel periodo</span> ({{ idle|length }}):</b> {{ idle|join(', ') }}</div>{% endif %}

<div class="foot">Sentinel TD{% if app_version %} v{{ app_version }}{% endif %} &middot; generato il {{ generated_at }}</div>
</body></html>
"""

_env = Environment(loader=BaseLoader(), autoescape=True)


async def render_pdf(data: dict, scope_label: str) -> tuple[bytes | None, str]:
    cfg = await rep.get_config()
    try:
        from .version import __version__ as ver
    except Exception:  # noqa: BLE001
        ver = ""
    html = _env.from_string(localize_template(DETAIL_TEMPLATE)).render(
        **data, logo=await rep._logo_data_uri(), company=cfg.get("company", ""),
        scope_label=scope_label, app_version=ver,
    )
    return rep.html_to_pdf(html), html


# ---------------------------------------------------------------- CSV (Excel)
def render_csv(data: dict) -> bytes:
    """Una sola tabella filtrabile in Excel: la colonna 'Sezione' distingue il
    riepilogo per componente dalla cronologia dei singoli aggiornamenti."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow([t(x) for x in ("Sezione", "Sito", "URL", "CMS", "Cartelle", "Componente", "Tipo", "Slug",
                                "Data", "Volte", "Falliti", "Da versione", "A versione", "Esito", "Errore")])
    riep, cron, ok_s, ko_s = t("Riepilogo"), t("Cronologia"), t("riuscito"), t("non riuscito")
    for s in data["sites"]:
        for c in s["components"]:
            w.writerow([riep, s["name"], s["url"], s["cms_label"], s["tags"], c["name"], c["type"],
                        c["slug"], "", c["count"], c["failed"], c["from"], c["to"], "", ""])
        for h in s["history"]:
            w.writerow([cron, s["name"], s["url"], s["cms_label"], s["tags"], h["name"], h["type"],
                        "", h["date"], 1, 0 if h["ok"] else 1, h["from"], h["to"],
                        ok_s if h["ok"] else ko_s, h["error"]])
    # BOM: Excel riconosce UTF-8 e gli accenti restano corretti
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
