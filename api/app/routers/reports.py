"""
API del report mensile (Sentinel → Report).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import UpdateMonthly
from ..auth import require_auth, verify_token
from .. import report as rep

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _strip_page_rule(html: str) -> str:
    """Toglie la regola @page (con i suoi margin box @bottom-center) dall'HTML.

    Serve solo per l'ANTEPRIMA a schermo: i browser non conoscono i margin box e
    riempiono la console di avvisi "Prevista una dichiarazione, invece e' stato
    rilevato @bottom-center". WeasyPrint invece li usa per numerare le pagine,
    quindi nel PDF la regola resta al suo posto.
    """
    out, i = [], 0
    while True:
        j = html.find("@page", i)
        if j == -1:
            out.append(html[i:])
            break
        k = html.find("{", j)
        if k == -1:
            out.append(html[i:])
            break
        depth, end = 1, k + 1
        while end < len(html) and depth:
            if html[end] == "{":
                depth += 1
            elif html[end] == "}":
                depth -= 1
            end += 1
        out.append(html[i:j])
        i = end
    return "".join(out)


@router.get("/config", dependencies=[Depends(require_auth)])
async def get_config():
    return {"config": await rep.get_config(), "template": await rep.get_template(),
            "default_template": rep.DEFAULT_TEMPLATE, "current_period": rep.current_period(),
            "suggested_period": rep.prev_period()}


@router.put("/config", dependencies=[Depends(require_auth)])
async def save_config(payload: dict = Body(...)):
    cfg = await rep.save_config(payload.get("config") or {})
    if "template" in payload:
        html = (payload.get("template") or "").strip()
        if html:
            # valida prima di salvare: un template rotto non deve bloccare l'invio mensile
            try:
                rep._env.from_string(html)
            except Exception as ex:  # noqa: BLE001
                raise HTTPException(422, f"Template non valido: {ex}")
            await rep.save_template(html)
        else:
            await rep.save_template(rep.DEFAULT_TEMPLATE)
    return {"ok": True, "config": cfg}


@router.get("/scopes", dependencies=[Depends(require_auth)])
async def scopes(s: AsyncSession = Depends(get_session)):
    """Report disponibili: quello globale + uno per ogni cartella/tag, con i siti dentro."""
    from ..models import Site
    sites = (await s.execute(select(Site))).scalars().all()
    tags: dict[str, int] = {}
    for x in sites:
        for t in (x.tags or "").split(","):
            t = t.strip()
            if t:
                tags[t] = tags.get(t, 0) + 1
    cfg = await rep.get_config()
    conf = {c["key"].lower(): c for c in cfg.get("scopes", [])}
    out = [{"key": rep.GLOBAL_KEY, "label": rep.scope_label(rep.GLOBAL_KEY), "sites": len(sites),
            "enabled": conf.get(rep.GLOBAL_KEY, {}).get("enabled", False)}]
    for t in sorted(tags, key=str.lower):
        out.append({"key": t, "label": t, "sites": tags[t], "enabled": conf.get(t.lower(), {}).get("enabled", False)})
    # cartelle configurate ma non piu' esistenti: mostrale comunque, cosi' si possono togliere
    for k, c in conf.items():
        if k != rep.GLOBAL_KEY and not any(x["key"].lower() == k for x in out):
            out.append({"key": c["key"], "label": c["key"] + " (cartella non più presente)", "sites": 0,
                        "enabled": c.get("enabled", False)})
    return out


@router.get("/trend", dependencies=[Depends(require_auth)])
async def trend(scope: str = Query(""), months: int = Query(12, ge=2, le=36), period: str = Query(""),
                s: AsyncSession = Depends(get_session)):
    """Serie mensile per il comparatore: totali per mese nel perimetro scelto."""
    from ..models import Site
    scope = scope or rep.GLOBAL_KEY
    allowed = None
    if scope != rep.GLOBAL_KEY:
        sites = (await s.execute(select(Site))).scalars().all()
        key = scope.strip().lower()
        allowed = {x.id for x in sites
                   if any(t.strip().lower() == key or t.strip().lower().startswith(key + "/")
                          for t in (x.tags or "").split(","))}
    end = period or rep.current_period()
    out = []
    for i in range(months - 1, -1, -1):
        out.append(await rep.month_stats(s, rep.shift_period(end, -i), allowed))
    # variazione rispetto al mese precedente della serie
    for i, row in enumerate(out):
        prev = out[i - 1]["updates"] if i else None
        row["delta"] = None if prev is None else row["updates"] - prev
        row["delta_pct"] = None if not prev else round((row["updates"] - prev) * 100 / prev)
    return {"scope": scope, "scope_label": rep.scope_label(scope), "months": out,
            "max": max([x["updates"] for x in out] + [1]),
            "total": sum(x["updates"] for x in out),
            "avg": round(sum(x["updates"] for x in out) / max(1, len(out)), 1)}


@router.get("/periods", dependencies=[Depends(require_auth)])
async def periods(s: AsyncSession = Depends(get_session)):
    """Mesi con dati, dal piu' recente."""
    rows = (await s.execute(
        select(UpdateMonthly.period, func.sum(UpdateMonthly.ok_count), func.count(func.distinct(UpdateMonthly.site_id)))
        .group_by(UpdateMonthly.period).order_by(UpdateMonthly.period.desc()).limit(36)
    )).all()
    out = [{"period": p, "label": rep.period_label(p), "updates": int(u or 0), "sites": int(n or 0)} for p, u, n in rows]
    cur = rep.current_period()
    if not any(x["period"] == cur for x in out):
        out.insert(0, {"period": cur, "label": rep.period_label(cur), "updates": 0, "sites": 0})
    return out


@router.post("/preview", dependencies=[Depends(require_auth)])
async def preview(payload: dict = Body(default={})):
    """HTML del report con la configurazione passata (non ancora salvata)."""
    period = payload.get("period") or rep.prev_period()
    scope = payload.get("scope") or rep.GLOBAL_KEY
    cfg = rep.normalize(payload.get("config")) if payload.get("config") else await rep.get_config()
    tpl = payload.get("template") if payload.get("template") else None
    try:
        html = await rep.render_html(period, template=tpl, cfg=cfg, scope=scope)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(422, f"Errore nel template: {ex}")
    # Solo a schermo: mostra il documento come un foglio A4 con i suoi margini.
    # media="screen" -> WeasyPrint (che stampa) lo ignora: il PDF resta identico.
    screen_css = (
        '<style media="screen">html{background:#e9edf2;padding:18px 0}'
        'body{max-width:210mm;margin:0 auto;padding:16mm 14mm;background:#fff;'
        'box-shadow:0 2px 14px rgba(0,0,0,.14);border-radius:2px}</style>'
    )
    html = _strip_page_rule(html)
    html = html.replace("</head>", screen_css + "</head>", 1) if "</head>" in html else screen_css + html
    return HTMLResponse(html)


@router.get("/pdf")
async def pdf(period: str = Query(...), k: str = Query(""), scope: str = Query("")):
    """Download del PDF (token in query: il browser scarica con un link diretto)."""
    if not verify_token(k):
        raise HTTPException(401, "Invalid token")
    html, blob, filename = await rep.build(period, scope or rep.GLOBAL_KEY)
    if blob is None:
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return Response(blob, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/send", dependencies=[Depends(require_auth)])
async def send_now(payload: dict = Body(default={})):
    """Invia un report. scope indicato = solo quello; scope assente = tutti quelli attivi."""
    period = payload.get("period") or rep.prev_period()
    scope = payload.get("scope")
    if scope:
        res = await send_report_for(period, scope)
        if not res["sent"]:
            raise HTTPException(400, res.get("error") or "Invio non riuscito: controlla SMTP e destinatari")
        return res
    results = await send_all(period)
    if not results:
        raise HTTPException(400, "Nessun report attivo da inviare: spunta almeno una voce")
    if not any(x["sent"] for x in results):
        raise HTTPException(400, results[0].get("error") or "Invio non riuscito")
    return {"sent": True, "results": results,
            "message": f"{sum(1 for x in results if x['sent'])} report inviati su {len(results)}"}


async def send_all(period: str, only_pending: bool = False, already: list[str] | None = None) -> list[dict]:
    """Invia tutti i report attivi. Con only_pending salta quelli gia' inviati (lista `already`)."""
    cfg = await rep.get_config()
    done = {x.lower() for x in (already or [])}
    out = []
    for sc in cfg.get("scopes", []):
        if not sc.get("enabled"):
            continue
        if only_pending and sc["key"].lower() in done:
            continue
        out.append(await send_report_for(period, sc["key"]))
    return out


async def send_report_for(period: str, scope: str = "") -> dict:
    """Genera e invia UN report (globale o di una cartella). Usata da API e cron mensile."""
    from ..notify import get_config as notif_config, render as notif_render
    from ..email import send_report as smtp_send

    scope = scope or rep.GLOBAL_KEY
    cfg = await rep.get_config()
    html, blob, filename = await rep.build(period, scope)
    data = await rep.gather(period, cfg, scope)
    top_lines = ", ".join(f"{t['name']} ({t['count']})" for t in data["top_items"][:5])
    ctx = {
        "period_label": data["period_label"], "company": cfg.get("company", ""),
        "total_updates": data["total_updates"], "total_failed": data["total_failed"],
        "sites_touched": data["sites_touched"], "sites_total": data["sites_total"],
        "distinct_items": data["distinct_items"],
        "top_lines": f"Più aggiornati: {top_lines}" if top_lines else "",
        "attachment": filename, "scope_label": data["scope_label"],
    }
    ncfg = await notif_config("monthly_report")
    r = notif_render("monthly_report", ncfg, ctx)
    if r["error"]:
        r = notif_render("monthly_report", (await notif_config("monthly_report")), ctx)
    mime = "application/pdf" if filename.endswith(".pdf") else "text/html"
    try:
        await smtp_send(r["subject"], r["body_email"], attachments=[(filename, mime, blob or html.encode("utf-8"))],
                        to=rep.report_recipients(cfg))
    except Exception as ex:  # noqa: BLE001
        return {"sent": False, "error": str(ex)[:200], "period": period, "scope": scope,
                "scope_label": data["scope_label"]}
    # notifica anche su Telegram se l'evento lo prevede (senza allegato)
    try:
        if ncfg.get("telegram"):
            from ..telegram import send_telegram
            await send_telegram(r["body_telegram"])
    except Exception:  # noqa: BLE001
        pass
    return {"sent": True, "period": period, "scope": scope, "scope_label": data["scope_label"],
            "filename": filename, "pdf": blob is not None,
            "updates": data["total_updates"], "sites": data["sites_touched"]}
