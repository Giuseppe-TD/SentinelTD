"""Invio email di report aggiornamenti via SMTP (aiosmtplib)."""
import html
import aiosmtplib
from email.message import EmailMessage
from email.utils import formataddr
from .config import settings
from .i18n import DEFAULT_LANGUAGE, t


def render_report(site_name: str, site_url: str, cms: str, results: list[dict]) -> tuple[str, str]:
    """Costruisce (subject, html) del report per un singolo sito."""
    ok = [r for r in results if r["ok"]]
    ko = [r for r in results if not r["ok"]]
    cms_label = "WordPress" if cms == "wp" else "Joomla"

    subject = f"[Sentinel] {site_name}: {len(ok)} {t('aggiornati', DEFAULT_LANGUAGE)}, {len(ko)} {t('falliti', DEFAULT_LANGUAGE)}"

    rows = []
    for r in results:
        name = html.escape(str(r["name"]))
        frm = html.escape(str(r.get("from", "")))
        to = html.escape(str(r.get("to", "")))
        if r["ok"]:
            badge = f'<span style="color:#1a7f4b;font-weight:600">{html.escape(t("aggiornato", DEFAULT_LANGUAGE))}</span>'
            ver = f"{frm} &rarr; {to}" if to and to != frm else (to or frm)
        else:
            err = html.escape(str(r.get("error", "")))
            badge = f'<span style="color:#b00020;font-weight:600">{html.escape(t("fallito", DEFAULT_LANGUAGE))}</span> <span style="color:#888">{err}</span>'
            ver = frm
        rows.append(
            f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">{name}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee;font-family:monospace">{ver}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{badge}</td></tr>'
        )

    body = f"""\
<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 4px">{html.escape(site_name)}</h2>
  <div style="color:#666;margin-bottom:14px">{cms_label} &middot; <a href="{html.escape(site_url)}">{html.escape(site_url)}</a></div>
  <table style="border-collapse:collapse;width:100%;font-size:14px">
    <thead><tr style="text-align:left;color:#888;font-size:12px">
      <th style="padding:6px 10px">{html.escape(t("Elemento", DEFAULT_LANGUAGE))}</th><th style="padding:6px 10px">{html.escape(t("Versione", DEFAULT_LANGUAGE))}</th><th style="padding:6px 10px">{html.escape(t("Esito", DEFAULT_LANGUAGE))}</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p style="color:#999;font-size:12px;margin-top:16px">{html.escape(t("Report automatico Sentinel TD", DEFAULT_LANGUAGE))}</p>
</div>"""
    return subject, body


async def send_report(subject: str, html_body: str, attachments: list[tuple[str, str, bytes]] | None = None,
                      to: str = "") -> None:
    """Invia il report. attachments: lista di (filename, mime, bytes).
    to: destinatari alternativi (virgola-separati); vuoto = REPORT_TO del .env."""
    recipients = (to or settings.REPORT_TO or "").strip()
    if not settings.SMTP_HOST or not recipients:
        return
    msg = EmailMessage()
    from_addr = settings.SMTP_FROM or settings.SMTP_USER
    from_name = settings.SMTP_FROM_NAME or "Sentinel TD"
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = recipients
    msg["Subject"] = subject
    msg.set_content(t("Questo report è disponibile in formato HTML.", DEFAULT_LANGUAGE))
    msg.add_alternative(html_body, subtype="html")
    for filename, mime, blob in (attachments or []):
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(blob, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)

    use_tls = settings.SMTP_PORT == 465       # SSL implicito
    start_tls = settings.SMTP_PORT == 587     # STARTTLS
    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASS or None,
        use_tls=use_tls,
        start_tls=start_tls if not use_tls else False,
    )
