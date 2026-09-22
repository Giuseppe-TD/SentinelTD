from ..i18n import template as localize_template
"""
Statistiche per la dashboard: classifiche, andamento e confronto fra mesi.

Legge il rollup mensile (update_monthly), quindi copre tutto lo storico disponibile
e non solo i 7 giorni della timeline.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, BaseLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import UpdateMonthly, Site
from ..auth import require_auth, verify_token
from .. import report as rep

router = APIRouter(prefix="/api/stats", tags=["stats"], dependencies=[Depends(require_auth)])

# Router SENZA guardia globale: il download PDF si autentica col token in query string
# (un <a href> o una nuova scheda non possono inviare l'header Authorization).
router_public = APIRouter(prefix="/api/stats", tags=["stats"])


async def _perimeter(s: AsyncSession, scope: str) -> tuple[set[int] | None, dict[int, Site]]:
    """Insieme di site_id dello scope (None = tutti) e mappa dei siti."""
    sites = (await s.execute(select(Site))).scalars().all()
    by_id = {x.id: x for x in sites}
    if not scope or scope == rep.GLOBAL_KEY:
        return None, by_id
    key = scope.strip().lower()
    ids = {x.id for x in sites
           if any(t.strip().lower() == key or t.strip().lower().startswith(key + "/")
                  for t in (x.tags or "").split(","))}
    return ids, by_id


async def _rows(s: AsyncSession, period: str, allowed: set[int] | None) -> list[UpdateMonthly]:
    rows = (await s.execute(select(UpdateMonthly).where(UpdateMonthly.period == period))).scalars().all()
    return [r for r in rows if allowed is None or r.site_id in allowed]


def _aggregate(rows: list[UpdateMonthly], by_id: dict[int, Site]) -> dict:
    sites: dict[int, dict] = {}
    comps: dict[str, dict] = {}
    types: dict[str, int] = {}
    for r in rows:
        if r.ok_count > 0:
            site = by_id.get(r.site_id)
            e = sites.setdefault(r.site_id, {
                "site_id": r.site_id, "name": r.site_name or (site.name if site else f"#{r.site_id}"),
                "cms": r.cms or (site.cms if site else ""), "updates": 0, "components": 0, "failed": 0,
            })
            e["updates"] += r.ok_count
            e["components"] += 1
            key = (r.ext_name or r.slug).lower()
            c = comps.setdefault(key, {"name": r.ext_name or r.slug, "type": r.ext_type, "count": 0, "sites": 0})
            c["count"] += r.ok_count
            c["sites"] += 1
            types[r.ext_type or "other"] = types.get(r.ext_type or "other", 0) + r.ok_count
        if r.fail_count > 0:
            site = by_id.get(r.site_id)
            e = sites.setdefault(r.site_id, {
                "site_id": r.site_id, "name": r.site_name or (site.name if site else f"#{r.site_id}"),
                "cms": r.cms or (site.cms if site else ""), "updates": 0, "components": 0, "failed": 0,
            })
            e["failed"] += r.fail_count
    return {
        "sites": sorted(sites.values(), key=lambda e: (-e["updates"], e["name"].lower())),
        "components": sorted(comps.values(), key=lambda c: (-c["count"], c["name"].lower())),
        "types": [{"type": k, "count": v} for k, v in sorted(types.items(), key=lambda kv: -kv[1])],
        "updates": sum(x["updates"] for x in sites.values()),
        "failed": sum(x["failed"] for x in sites.values()),
        "sites_touched": sum(1 for x in sites.values() if x["updates"] > 0),
        "distinct": len(comps),
    }


@router.get("/overview")
async def overview(period: str = Query(""), scope: str = Query(""), s: AsyncSession = Depends(get_session)):
    """Classifiche e totali di un mese, con variazione rispetto al mese precedente."""
    period = period or rep.current_period()
    allowed, by_id = await _perimeter(s, scope)
    cur = _aggregate(await _rows(s, period, allowed), by_id)
    prev_period = rep.shift_period(period, -1)
    prev = _aggregate(await _rows(s, prev_period, allowed), by_id)
    prev_by_site = {x["site_id"]: x["updates"] for x in prev["sites"]}
    for x in cur["sites"]:
        x["prev"] = prev_by_site.get(x["site_id"], 0)
        x["delta"] = x["updates"] - x["prev"]
    return {
        "period": period, "period_label": rep.period_label(period),
        "prev_period": prev_period, "prev_label": rep.period_label(prev_period),
        "scope": scope or rep.GLOBAL_KEY, "scope_label": rep.scope_label(scope),
        "totals": {"updates": cur["updates"], "failed": cur["failed"],
                   "sites": cur["sites_touched"], "components": cur["distinct"]},
        "prev_totals": {"updates": prev["updates"], "failed": prev["failed"],
                        "sites": prev["sites_touched"], "components": prev["distinct"]},
        "top_sites": cur["sites"][:25],
        "top_components": cur["components"][:20],
        "by_type": cur["types"],
        "max_site": max([x["updates"] for x in cur["sites"][:25]] + [1]),
        "max_component": max([x["count"] for x in cur["components"][:20]] + [1]),
    }


@router.get("/compare")
async def compare(a: str = Query(...), b: str = Query(...), scope: str = Query(""),
                  s: AsyncSession = Depends(get_session)):
    """Confronto fra due mesi qualsiasi, sito per sito e componente per componente."""
    allowed, by_id = await _perimeter(s, scope)
    ag_a = _aggregate(await _rows(s, a, allowed), by_id)
    ag_b = _aggregate(await _rows(s, b, allowed), by_id)
    map_a = {x["site_id"]: x for x in ag_a["sites"]}
    map_b = {x["site_id"]: x for x in ag_b["sites"]}
    rows = []
    for sid in set(map_a) | set(map_b):
        va = map_a.get(sid, {}).get("updates", 0)
        vb = map_b.get(sid, {}).get("updates", 0)
        name = (map_a.get(sid) or map_b.get(sid))["name"]
        cms = (map_a.get(sid) or map_b.get(sid))["cms"]
        rows.append({"site_id": sid, "name": name, "cms": cms, "a": va, "b": vb, "delta": vb - va,
                     "pct": None if va == 0 else round((vb - va) * 100 / va)})
    rows.sort(key=lambda r: (-abs(r["delta"]), -r["b"], r["name"].lower()))

    ca = {c["name"].lower(): c for c in ag_a["components"]}
    cb = {c["name"].lower(): c for c in ag_b["components"]}
    crows = []
    for k in set(ca) | set(cb):
        va = ca.get(k, {}).get("count", 0)
        vb = cb.get(k, {}).get("count", 0)
        crows.append({"name": (ca.get(k) or cb.get(k))["name"], "a": va, "b": vb, "delta": vb - va})
    crows.sort(key=lambda r: (-abs(r["delta"]), -r["b"]))

    return {
        "a": {"period": a, "label": rep.period_label(a), "updates": ag_a["updates"],
              "sites": ag_a["sites_touched"], "components": ag_a["distinct"], "failed": ag_a["failed"]},
        "b": {"period": b, "label": rep.period_label(b), "updates": ag_b["updates"],
              "sites": ag_b["sites_touched"], "components": ag_b["distinct"], "failed": ag_b["failed"]},
        "scope_label": rep.scope_label(scope),
        "sites": rows[:40],
        "components": crows[:25],
        "max": max([r["a"] for r in rows] + [r["b"] for r in rows] + [1]),
    }


# ---------------------------------------------------------------- export PDF
_PDF_TPL = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<style>
  @page { size: A4; margin: 16mm 14mm; }
  body { font: 11px/1.5 Helvetica, Arial, sans-serif; color: #24292f; margin: 0; }
  .head { display: flex; align-items: center; border-bottom: 2px solid #1f6feb; padding-bottom: 12px; margin-bottom: 16px; }
  .head img { max-height: 44px; max-width: 190px; }
  .head .t { margin-left: auto; text-align: right; }
  .head h1 { font-size: 16px; margin: 0 0 2px; }
  .head .p { color: #6a737d; font-size: 11px; }
  h2 { font-size: 13px; margin: 18px 0 8px; color: #1f2933; border-left: 3px solid #1f6feb; padding-left: 8px; }
  .kpi { display: flex; gap: 10px; margin-bottom: 8px; }
  .kpi div { flex: 1; border: 1px solid #e1e4e8; border-radius: 6px; padding: 9px 11px; }
  .kpi .v { font-size: 19px; font-weight: 700; color: #1f6feb; }
  .kpi .k { font-size: 9.5px; color: #6a737d; text-transform: uppercase; letter-spacing: .04em; }
  .kpi .d { font-size: 9.5px; color: #6a737d; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
  th { text-align: left; font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em; color: #6a737d;
       border-bottom: 1px solid #d0d7de; padding: 5px 6px; }
  td { padding: 5px 6px; border-bottom: 1px solid #eef1f4; vertical-align: middle; }
  .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .mut { color: #6a737d; }
  .up { color: #1a7f4b; } .dn { color: #b00020; }
  .bar { height: 7px; background: #eef1f4; border-radius: 99px; overflow: hidden; width: 150px; }
  .bar i { display: block; height: 100%; border-radius: 99px; }
  .foot { margin-top: 20px; padding-top: 9px; border-top: 1px solid #e1e4e8; color: #8a949e; font-size: 9.5px; }
</style></head><body>
<div class="head">
  {% if logo %}<img src="{{ logo }}">{% endif %}
  <div class="t"><h1>Statistiche aggiornamenti</h1>
    <div class="p">{{ subtitle }}{% if company %} &middot; {{ company }}{% endif %}</div>
    {% if scope_label != 'Tutti i siti' %}<div class="p" style="font-weight:600;color:#1f6feb">{{ scope_label }}</div>{% endif %}
  </div>
</div>

{% if mode == 'month' %}
<div class="kpi">
  <div><div class="v">{{ t.updates }}</div><div class="k">Aggiornamenti</div><div class="d">{{ d.updates }} vs {{ prev_label }}</div></div>
  <div><div class="v">{{ t.sites }}</div><div class="k">Siti aggiornati</div><div class="d">{{ d.sites }}</div></div>
  <div><div class="v">{{ t.components }}</div><div class="k">Componenti diversi</div><div class="d">{{ d.components }}</div></div>
  <div><div class="v">{{ t.failed }}</div><div class="k">Non riusciti</div><div class="d">{{ d.failed }}</div></div>
</div>

<h2>Siti più aggiornati</h2>
<table><thead><tr><th>Sito</th><th>CMS</th><th class="n">Aggiornamenti</th><th class="n">Variazione</th><th></th></tr></thead><tbody>
{% for s in sites %}
  <tr><td>{{ s.name }}</td><td class="mut">{{ 'WordPress' if s.cms == 'wp' else 'Joomla' }}</td>
      <td class="n">{{ s.updates }}</td>
      <td class="n {% if s.delta > 0 %}up{% elif s.delta < 0 %}dn{% endif %}">{{ '+' if s.delta > 0 else '' }}{{ s.delta }}</td>
      <td><div class="bar"><i style="width:{{ s.pct }}%;background:{{ s.color }}"></i></div></td></tr>
{% endfor %}
</tbody></table>
{% if not sites %}<div class="mut">Nessun aggiornamento nel periodo.</div>{% endif %}

<h2>Componenti più aggiornati</h2>
<table><thead><tr><th>Componente</th><th>Tipo</th><th class="n">Aggiornamenti</th><th class="n">Siti</th><th></th></tr></thead><tbody>
{% for c in components %}
  <tr><td>{{ c.name }}</td><td class="mut">{{ c.type }}</td><td class="n">{{ c.count }}</td><td class="n">{{ c.sites }}</td>
      <td><div class="bar"><i style="width:{{ c.pct }}%;background:{{ c.color }}"></i></div></td></tr>
{% endfor %}
</tbody></table>

{% else %}
<div class="kpi">
  <div><div class="v">{{ a.updates }}</div><div class="k">{{ a.label }}</div><div class="d">{{ a.sites }} siti &middot; {{ a.components }} componenti</div></div>
  <div><div class="v">{{ b.updates }}</div><div class="k">{{ b.label }}</div><div class="d">{{ b.sites }} siti &middot; {{ b.components }} componenti</div></div>
  <div><div class="v {% if diff > 0 %}up{% elif diff < 0 %}dn{% endif %}">{{ '+' if diff > 0 else '' }}{{ diff }}</div>
       <div class="k">Variazione</div><div class="d">aggiornamenti totali</div></div>
  <div><div class="v">{{ b.failed }}</div><div class="k">Non riusciti</div><div class="d">erano {{ a.failed }}</div></div>
</div>

<h2>Confronto per sito</h2>
<table><thead><tr><th>Sito</th><th class="n">{{ a.label }}</th><th class="n">{{ b.label }}</th><th class="n">Variazione</th></tr></thead><tbody>
{% for r in rows %}
  <tr><td>{{ r.name }}</td><td class="n">{{ r.a }}</td><td class="n">{{ r.b }}</td>
      <td class="n {% if r.delta > 0 %}up{% elif r.delta < 0 %}dn{% endif %}">{{ '+' if r.delta > 0 else '' }}{{ r.delta }}{% if r.pct is not none %} ({{ '+' if r.delta > 0 else '' }}{{ r.pct }}%){% endif %}</td></tr>
{% endfor %}
</tbody></table>

<h2>Confronto per componente</h2>
<table><thead><tr><th>Componente</th><th class="n">{{ a.label }}</th><th class="n">{{ b.label }}</th><th class="n">Variazione</th></tr></thead><tbody>
{% for c in crows %}
  <tr><td>{{ c.name }}</td><td class="n">{{ c.a }}</td><td class="n">{{ c.b }}</td>
      <td class="n {% if c.delta > 0 %}up{% elif c.delta < 0 %}dn{% endif %}">{{ '+' if c.delta > 0 else '' }}{{ c.delta }}</td></tr>
{% endfor %}
</tbody></table>
{% endif %}

<div class="foot">Sentinel TD{% if app_version %} v{{ app_version }}{% endif %} &middot; <a href="{{ vendor_url }}" style="color:inherit;text-decoration:none">{{ vendor }}</a> &mdash; {{ author }} &middot; generato il {{ generated_at }}</div>
</body></html>"""

_env = Environment(loader=BaseLoader(), autoescape=True)


def _palette(i: int) -> str:
    """Stessi colori della schermata: angolo aureo, tinte ben distanziate."""
    h = round((i * 137.508 + 16) % 360)
    return f"hsl({h} {62 + (i % 3) * 7}% {52 + (i % 2) * 6}%)"


def _fmt_delta(cur: int, prev: int) -> str:
    diff = cur - prev
    pct = None if prev == 0 else round(diff * 100 / prev)
    return ("+" if diff > 0 else "") + str(diff) + ("" if pct is None else f" ({'+' if diff > 0 else ''}{pct}%)")


@router_public.get("/pdf")
async def stats_pdf(
    mode: str = Query("month"),
    period: str = Query(""),
    a: str = Query(""),
    b: str = Query(""),
    scope: str = Query(""),
    k: str = Query(""),
    s: AsyncSession = Depends(get_session),
):
    """PDF della vista Statistiche (mese corrente oppure confronto A/B).
    Token in query come per le altre risorse scaricabili con link diretto."""
    if not verify_token(k):
        raise HTTPException(401, "Invalid token")

    from ..version import __version__ as _ver, VENDOR, VENDOR_URL, AUTHOR
    cfg = await rep.get_config()
    ctx = {
        "app_version": _ver, "vendor": VENDOR, "vendor_url": VENDOR_URL, "author": AUTHOR,
        "logo": await rep._logo_data_uri(),
        "company": cfg.get("company", ""),
        "scope_label": rep.scope_label(scope),
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "mode": "compare" if mode == "compare" else "month",
    }

    if ctx["mode"] == "month":
        data = await overview(period=period, scope=scope, s=s)
        t, p = data["totals"], data["prev_totals"]
        top_sites = data["top_sites"]
        maxs = data["max_site"] or 1
        maxc = data["max_component"] or 1
        ctx.update({
            "subtitle": data["period_label"],
            "prev_label": data["prev_label"],
            "t": t,
            "d": {kk: _fmt_delta(t[kk], p[kk]) for kk in ("updates", "sites", "components", "failed")},
            "sites": [{**x, "pct": max(2, round(x["updates"] * 100 / maxs)), "color": _palette(i)}
                      for i, x in enumerate(top_sites)],
            "components": [{**x, "pct": max(2, round(x["count"] * 100 / maxc)), "color": _palette(i)}
                           for i, x in enumerate(data["top_components"])],
        })
        fname = f"statistiche-{data['period']}-{rep.scope_slug(scope)}"
    else:
        if not a or not b:
            raise HTTPException(422, "Per il confronto servono i due periodi (a, b)")
        data = await compare(a=a, b=b, scope=scope, s=s)
        ctx.update({
            "subtitle": f"{data['a']['label']} vs {data['b']['label']}",
            "a": data["a"], "b": data["b"],
            "diff": data["b"]["updates"] - data["a"]["updates"],
            "rows": data["sites"], "crows": data["components"],
        })
        fname = f"statistiche-{a}-vs-{b}-{rep.scope_slug(scope)}"

    html = _env.from_string(localize_template(_PDF_TPL)).render(**ctx)
    blob = rep.html_to_pdf(html)
    if blob is None:
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{fname}.html"'})
    return Response(blob, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})
