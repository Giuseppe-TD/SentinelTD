"""
Engine notifiche di Sentinel TD.

Ogni EVENTO ha: canali (email/telegram) attivabili, un oggetto e un corpo email (HTML),
un corpo Telegram (HTML-mode di Telegram), e un set di VARIABILI documentate. I template
sono Jinja2, editabili dal pannello e salvati in app_settings (chiave "notif:<evento>");
se non c'e' nulla di salvato valgono i default qui sotto (che sono anche il "ripristina").

Il worker chiama dispatch(event, ctx): l'engine carica la config, renderizza e invia sui
canali attivi. Le variabili "ricche" (tabelle/liste gia' formattate) evitano all'utente di
scrivere cicli Jinja: {{ updates_table }} per l'email, {{ updates_lines }} per Telegram.
"""
import html
import json
import logging
from typing import Any

from jinja2 import Environment, BaseLoader, TemplateError

from .db import SessionLocal
from .models import AppSetting
from .email import send_report
from .telegram import send_telegram
from .config import settings

from .i18n import DEFAULT_LANGUAGE, normalize_language, t, template as localize_template

log = logging.getLogger("notify")

# ------------------------------------------------------------------ eventi
EVENTS: dict[str, dict[str, Any]] = {
    "site_report": {
        "label": "Report update per sito",
        "desc": "Inviato al termine degli update di un sito (riusciti e falliti).",
        "channels": {"email": True, "telegram": False},
        "vars": {
            "site_name": "Nome del sito", "site_url": "URL del sito", "cms": "WordPress / Joomla",
            "ok_count": "Update riusciti", "failed_count": "Update falliti", "date": "Data e ora",
            "updates_table": "Tabella HTML degli update (per email)",
            "updates_lines": "Elenco testuale degli update (per Telegram)",
            "results": "Lista grezza [{name, from, to, ok, error}] per template avanzati",
        },
        "subject": "[Sentinel] {{ site_name }}: {{ ok_count }} aggiornati, {{ failed_count }} falliti",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 4px">{{ site_name }}</h2>
  <div style="color:#666;margin-bottom:14px">{{ cms }} &middot; <a href="{{ site_url }}">{{ site_url }}</a></div>
  {{ updates_table }}
  <p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {{ date }}</p>
</div>""",
        "telegram": "🛠 <b>{{ site_name }}</b> — {{ ok_count }} aggiornati, {{ failed_count }} falliti\n{{ updates_lines }}",
    },
    "update_failed": {
        "label": "Update fallito",
        "desc": "Un singolo update non e' riuscito (con il motivo).",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome del sito", "site_url": "URL", "item": "Estensione", "reason": "Motivo dell'errore", "date": "Data e ora"},
        "subject": "[Sentinel] Update fallito su {{ site_name }}: {{ item }}",
        "email": """<p><b>{{ site_name }}</b> — update fallito: <b>{{ item }}</b></p><p style="color:#b00020">{{ reason }}</p><p><a href="{{ site_url }}">{{ site_url }}</a></p>""",
        "telegram": "❌ <b>{{ site_name }}</b> — update fallito: {{ item }}\n<i>{{ reason }}</i>",
    },
    "cycle_summary": {
        "label": "Riepilogo ciclo",
        "desc": "Un messaggio a fine ciclo automatico (solo se c'e' stato qualcosa da applicare).",
        "channels": {"email": False, "telegram": True},
        "vars": {"applied": "Update applicati", "sites_touched": "Siti coinvolti", "failed": "Update falliti",
                 "ok_lines": "Elenco successi (una riga per sito)", "failed_lines": "Elenco fallimenti", "date": "Data e ora"},
        "subject": "[Sentinel] Ciclo completato: {{ applied }} update su {{ sites_touched }} siti",
        "email": """<h3>Ciclo aggiornamenti completato</h3><p>✅ {{ applied }} update applicati su {{ sites_touched }} siti</p><pre style="font-family:inherit">{{ ok_lines }}</pre>{% if failed %}<p>❌ {{ failed }} falliti</p><pre style="font-family:inherit">{{ failed_lines }}</pre>{% endif %}""",
        "telegram": "📊 <b>Ciclo aggiornamenti completato</b>\n✅ {{ applied }} update applicati su {{ sites_touched }} siti\n{{ ok_lines }}{% if failed %}\n\n❌ {{ failed }} falliti\n{{ failed_lines }}{% endif %}",
    },
    "site_offline": {
        "label": "Sito non raggiungibile",
        "desc": "Il sito risulta offline dopo piu' check consecutivi.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome", "site_url": "URL", "reason": "Errore riscontrato", "attempts": "Check falliti consecutivi", "window_min": "Minuti dal primo fallimento", "date": "Data e ora"},
        "subject": "[Sentinel] {{ site_name }} non raggiungibile",
        "email": """<p>🔴 <b>{{ site_name }}</b> non raggiungibile<br><a href="{{ site_url }}">{{ site_url }}</a></p><p>{{ reason }}</p><p style="color:#666">Confermato dopo {{ attempts }} check in ~{{ window_min }} min</p>""",
        "telegram": "🔴 <b>{{ site_name }}</b> non raggiungibile\n{{ site_url }}\nConfermato dopo {{ attempts }} check in ~{{ window_min }} min\n<i>{{ reason }}</i>",
    },
    "site_online": {
        "label": "Sito di nuovo raggiungibile",
        "desc": "Il sito e' tornato online.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome", "site_url": "URL", "date": "Data e ora"},
        "subject": "[Sentinel] {{ site_name }} di nuovo online",
        "email": """<p>🟢 <b>{{ site_name }}</b> di nuovo raggiungibile<br><a href="{{ site_url }}">{{ site_url }}</a></p>""",
        "telegram": "🟢 <b>{{ site_name }}</b> di nuovo raggiungibile\n{{ site_url }}",
    },
    "monthly_report": {
        "label": "Report mensile",
        "desc": "Email di fine mese con il PDF del report allegato.",
        "channels": {"email": True, "telegram": False},
        "vars": {
            "period_label": "Mese del report (es. Agosto 2026)", "company": "Ragione sociale nel report",
            "total_updates": "Aggiornamenti applicati nel mese", "total_failed": "Aggiornamenti non riusciti",
            "sites_touched": "Siti aggiornati", "sites_total": "Siti monitorati",
            "distinct_items": "Componenti diversi aggiornati", "top_lines": "Top componenti (elenco testuale)",
            "attachment": "Nome del file allegato", "date": "Data e ora di generazione",
            "scope_label": "Ambito del report (Tutti i siti oppure il nome della cartella)",
        },
        "subject": "{{ company }} — report manutenzione {{ period_label }}{% if scope_label and not scope_global %} · {{ scope_label }}{% endif %}",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 6px">Report manutenzione — {{ period_label }}</h2>
  {% if scope_label and not scope_global %}<div style="color:#666;margin:-4px 0 10px">{{ scope_label }}</div>{% endif %}
  <p>Nel periodo sono stati applicati <b>{{ total_updates }}</b> aggiornamenti su <b>{{ sites_touched }}</b> siti
  ({{ distinct_items }} componenti diversi){% if total_failed %}, con {{ total_failed }} tentativi non riusciti e ritentati automaticamente{% endif %}.</p>
  {% if top_lines %}<p style="color:#555">{{ top_lines }}</p>{% endif %}
  <p>Il dettaglio completo, sito per sito, è nel PDF allegato (<b>{{ attachment }}</b>).</p>
  <p style="color:#999;font-size:12px;margin-top:18px">Sentinel TD &middot; {{ date }}</p>
</div>""",
        "telegram": "📄 <b>Report {{ period_label }}</b>\n{{ total_updates }} aggiornamenti su {{ sites_touched }} siti\nPDF inviato per email.",
    },
    "vuln_alert": {
        "label": "Vulnerabilita' rilevata",
        "desc": "Il Centro Sicurezza ha trovato una nuova vulnerabilita' su un'estensione installata.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome", "site_url": "URL", "ext_name": "Estensione", "ext_version": "Versione installata",
                 "cve_id": "CVE", "title": "Titolo", "severity": "Gravita'", "cvss": "Punteggio CVSS", "exploited": "Sfruttata attivamente (si/no)",
                 "version_fixed": "Versione che corregge", "url": "Link alla scheda", "date": "Data e ora"},
        "subject": "[Sentinel] {{ severity|upper }}: {{ cve_id }} su {{ site_name }}",
        "email": """<p>🚨 <b>{{ severity|upper }}</b> {{ cve_id }} — {{ title }}</p><p>🌐 <b>{{ site_name }}</b> · {{ ext_name }} <code>{{ ext_version }}</code>{% if version_fixed %}<br>Corretta in: <b>{{ version_fixed }}</b>{% endif %}</p><p><a href="{{ url }}">{{ url }}</a></p>""",
        "telegram": "🚨 <b>{{ severity|upper }}</b> {{ cve_id }}\n{{ title }}\n🌐 <b>{{ site_name }}</b>\n🧩 {{ ext_name }} <code>{{ ext_version }}</code>{% if version_fixed %}\n✅ corretta in {{ version_fixed }}{% endif %}{% if exploited == 'si' %}\n⚠️ sfruttata attivamente{% endif %}\n🔗 {{ url }}",
    },
    "expiry_alert": {
        "label": "Scadenza / rinnovo",
        "desc": "Dominio oppure plugin/tema/licenza in scadenza. Le soglie si configurano in Impostazioni.",
        "channels": {"email": False, "telegram": True},
        "vars": {
            "site_name": "Sito/siti collegati (solo domini)", "site_url": "URL sito (solo domini)",
            "kind": "Tipo di scadenza", "item": "Dominio, plugin, tema o licenza",
            "platform": "WordPress / Joomla / Entrambi", "provider": "Fornitore",
            "expires_on": "Data di scadenza", "days": "Giorni mancanti",
            "notes": "Note", "date": "Data e ora dell'avviso",
        },
        "subject": "[Sentinel] {{ item }} scade tra {{ days }} giorni",
        "email": """<div style="font-family:Inter,Arial,sans-serif;background:#f5f7fb;padding:24px;color:#172033">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e7eaf0;border-radius:14px;overflow:hidden">
    <div style="padding:18px 22px;background:#111827;color:#fff"><div style="font-size:12px;opacity:.7">SENTINEL TD</div><div style="font-size:20px;font-weight:700;margin-top:3px">⏳ Scadenza in arrivo</div></div>
    <div style="padding:22px">
      <div style="font-size:22px;font-weight:750;margin-bottom:8px">{{ item }}</div>
      <div style="font-size:15px;color:#596174;margin-bottom:18px">{{ kind }}{% if platform %} · {{ platform }}{% endif %}</div>
      <div style="padding:14px 16px;border-radius:10px;background:#fff7ed;border:1px solid #fed7aa;margin-bottom:16px">
        Scade il <b>{{ expires_on }}</b> · <b>{{ days }} giorni</b> rimanenti
      </div>
      {% if site_name %}<p style="margin:8px 0"><b>Sito/i:</b> {{ site_name }}</p>{% endif %}
      {% if provider %}<p style="margin:8px 0"><b>Fornitore:</b> {{ provider }}</p>{% endif %}
      {% if notes %}<p style="margin:12px 0;color:#596174">{{ notes }}</p>{% endif %}
      {% if site_url %}<p style="margin:16px 0 0"><a href="{{ site_url }}" style="color:#2563eb">{{ site_url }}</a></p>{% endif %}
    </div>
  </div>
</div>""",
        "telegram": "⏳ <b>{{ item }}</b>\n{{ kind }}{% if platform %} · {{ platform }}{% endif %}\n📅 <b>{{ expires_on }}</b> — tra <b>{{ days }} giorni</b>{% if site_name %}\n🌐 {{ site_name }}{% endif %}{% if provider %}\n🏢 {{ provider }}{% endif %}{% if notes %}\n<i>{{ notes }}</i>{% endif %}",
    },
}

# The dictionaries above stay in the Italian source language. They are localized
# when requested so the notification editor follows the browser language while
# background jobs keep using DEFAULT_UI_LANGUAGE.
def event_meta(event: str, language: str | None = None) -> dict:
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    e = EVENTS[event]
    return {
        "label": localize_template(e["label"], lang),
        "desc": localize_template(e["desc"], lang),
        "vars": {key: t(value, lang) for key, value in e["vars"].items()},
    }


def sample_context(event: str, language: str | None = None) -> dict:
    """Localized application-owned sample values used only by preview/test."""
    lang = normalize_language(language, DEFAULT_LANGUAGE)

    def walk(value):
        if isinstance(value, str):
            return t(value, lang)
        if isinstance(value, list):
            return [walk(x) for x in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return walk(SAMPLE[event])

# ------------------------------------------------------------------ dati di esempio (anteprima/test)
SAMPLE: dict[str, dict[str, Any]] = {
    "site_report": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "cms": "WordPress",
                    "results": [{"name": "WooCommerce", "from": "11.0.0", "to": "11.0.1", "ok": True, "error": ""},
                                {"name": "Yoast SEO", "from": "28.3", "to": "28.4", "ok": True, "error": ""},
                                {"name": "ACF", "from": "6.8.7", "to": "6.8.8", "ok": False, "error": "Errore di filesystem"}]},
    "update_failed": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "item": "WPForms Lite 2.0.0.3 -> 2.0.0.4", "reason": "Errore di filesystem"},
    "cycle_summary": {"applied": 9, "sites_touched": 7, "failed": 1,
                      "ok_lines": "• Sito A: WooCommerce 11.0.0→11.0.1\n• Sito B: Traduzioni (1)\n• Sito C: Yoast 28.3→28.4",
                      "failed_lines": "• Sito D: ACF 6.8.7→6.8.8"},
    "site_offline": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "reason": "HTTP 503", "attempts": 3, "window_min": 12},
    "site_online": {"site_name": "Sito di prova", "site_url": "https://esempio.it"},
    "monthly_report": {"period_label": "Agosto 2026", "company": "Tastiere Digitali", "total_updates": 128,
                       "total_failed": 2, "sites_touched": 31, "sites_total": 42, "distinct_items": 24,
                       "top_lines": "Più aggiornati: WooCommerce (14), YOOtheme (11), Akeeba Backup (9)",
                       "attachment": "report-2026-08-globale.pdf", "scope_label": "Tutti i siti"},
    "vuln_alert": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "ext_name": "Contact Form 7", "ext_version": "5.9.2",
                   "cve_id": "CVE-2026-12345", "title": "Stored XSS in form fields", "severity": "high", "cvss": 7.5,
                   "exploited": "no", "version_fixed": "5.9.3", "url": "https://www.cve.org/CVERecord?id=CVE-2026-12345"},
    "expiry_alert": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "kind": "Dominio",
                     "item": "esempio.it", "platform": "", "provider": "Registro dominio",
                     "expires_on": "15/10/2026", "days": 30, "notes": "Rinnovo automatico disattivato"},
}

_env = Environment(loader=BaseLoader(), autoescape=False)


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


def default_config(event: str, language: str | None = None) -> dict:
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    e = EVENTS[event]
    return {
        "enabled": True,
        "email": e["channels"]["email"],
        "telegram": e["channels"]["telegram"],
        "subject": localize_template(e["subject"], lang),
        "body_email": localize_template(e["email"], lang),
        "body_telegram": localize_template(e["telegram"], lang),
    }


async def get_config(event: str, language: str | None = None) -> dict:
    """Effective config: localized default overridden by a saved custom template."""
    cfg = default_config(event, language)
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, f"notif:{event}")
            if row and row.value:
                saved = json.loads(row.value)
                for k in cfg:
                    if k in saved:
                        cfg[k] = saved[k]
    except Exception as ex:  # noqa: BLE001
        log.warning("notify: config %s non leggibile: %s", event, ex)
    return cfg


def enrich(event: str, ctx: dict, escape: bool = True, language: str | None = None) -> dict:
    """Add rich variables and localize app-owned values, never free-form user content."""
    from datetime import datetime

    lang = normalize_language(language, DEFAULT_LANGUAGE)
    out = dict(ctx)
    out.setdefault("date", datetime.now().strftime("%d/%m/%Y %H:%M"))
    raw_scope = str(ctx.get("scope_label", "") or "").strip()
    out["scope_global"] = raw_scope in {"", "Tutti i siti", "All sites", "Tous les sites", "Alle Websites"}

    # These values are produced by Sentinel itself (not written by the end user).
    for k in ("kind", "platform", "provider", "reason", "scope_label", "period_label", "top_lines", "severity"):
        if k in out and isinstance(out[k], str):
            out[k] = t(out[k], lang)

    if event == "site_report":
        results = out.get("results") or []
        out["ok_count"] = sum(1 for r in results if r.get("ok"))
        out["failed_count"] = sum(1 for r in results if not r.get("ok"))
        rows, lines = [], []
        for r in results:
            name, frm, to = _esc(r.get("name")), _esc(r.get("from")), _esc(r.get("to"))
            if r.get("ok"):
                badge = '<span style="color:#1a7f4b;font-weight:600">' + t("aggiornato", lang) + "</span>"
                ver = f"{frm} &rarr; {to}" if to and to != frm else (to or frm)
                lines.append(f"✅ {name} {frm}→{to}" if to and to != frm else f"✅ {name}")
            else:
                error = t(str(r.get("error") or ""), lang)
                badge = f'<span style="color:#b00020;font-weight:600">{t("fallito", lang)}</span> <span style="color:#888">{_esc(error)}</span>'
                ver = frm
                lines.append(f"❌ {name} — {_esc(error)}")
            rows.append(
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">{name}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;font-family:monospace">{ver}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{badge}</td></tr>'
            )
        out["updates_table"] = (
            '<table style="border-collapse:collapse;width:100%;font-size:14px"><thead><tr style="text-align:left;color:#888;font-size:12px">'
            f'<th style="padding:6px 10px">{t("Elemento", lang)}</th>'
            f'<th style="padding:6px 10px">{t("Versione", lang)}</th>'
            f'<th style="padding:6px 10px">{t("Esito", lang)}</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )
        out["updates_lines"] = "\n".join(lines)
        out["cms"] = "WordPress" if str(out.get("cms", "")).lower() in ("wp", "wordpress") else ("Joomla" if out.get("cms") else "")

    if not escape:
        return out
    for k in (
        "site_name", "site_url", "item", "reason", "ext_name", "ext_version", "cve_id", "title", "url",
        "ok_lines", "failed_lines", "version_fixed", "kind", "platform", "provider", "expires_on", "notes",
        "period_label", "scope_label", "top_lines",
    ):
        if k in out and isinstance(out[k], str):
            out[k] = _esc(out[k])
    return out


def render(event: str, cfg: dict, ctx: dict, language: str | None = None) -> dict:
    """Return {subject, body_email, body_telegram, error}."""
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    data = enrich(event, ctx, language=lang)
    data_plain = enrich(event, ctx, escape=False, language=lang)
    res = {"subject": "", "body_email": "", "body_telegram": "", "error": ""}
    for key in ("subject", "body_email", "body_telegram"):
        try:
            res[key] = _env.from_string(cfg.get(key) or "").render(**(data_plain if key == "subject" else data))
        except TemplateError as ex:
            res["error"] = f"{key}: {ex}"
            res[key] = ""
    return res


async def dispatch(event: str, ctx: dict) -> dict:
    """Send a background event using DEFAULT_UI_LANGUAGE or a saved custom template."""
    sent = {"email": False, "telegram": False}
    if event not in EVENTS:
        return sent
    lang = DEFAULT_LANGUAGE
    cfg = await get_config(event, lang)
    if not cfg.get("enabled", True):
        return sent
    r = render(event, cfg, ctx, lang)
    if r["error"]:
        log.warning("notify %s: template non valido (%s) — uso i default", event, r["error"])
        r = render(event, default_config(event, lang), ctx, lang)
    if cfg.get("email"):
        try:
            await send_report(r["subject"], r["body_email"])
            sent["email"] = True
        except Exception as ex:  # noqa: BLE001
            log.warning("notify %s: email fallita: %s", event, ex)
    if cfg.get("telegram"):
        try:
            sent["telegram"] = bool(await send_telegram(r["body_telegram"]))
        except Exception as ex:  # noqa: BLE001
            log.warning("notify %s: telegram fallito: %s", event, ex)
    return sent
