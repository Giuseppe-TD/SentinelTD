"""
Notifiche Telegram per Panopticon Lite.

Filosofia (richiesta da Giuseppe): NON spammare. Due tipi di messaggio:
  A) Notifiche immediate solo per i PROBLEMI:
     - update fallito su un sito
     - sito andato OFFLINE (transizione ok->error)
     - sito tornato ONLINE (transizione error->ok)
  B) Riepilogo aggregato dopo ogni ciclo di auto-update:
     - un solo messaggio: "N update applicati su M siti, K falliti"
     - inviato solo se e' successo qualcosa (zero update = nessun messaggio)

Tutto e' best-effort: se Telegram e' irraggiungibile o la config manca, le funzioni
NON sollevano eccezioni (un alert fallito non deve mai far fallire un update o un check).
"""
import logging

import httpx

from .config import settings
from .i18n import DEFAULT_LANGUAGE, t

log = logging.getLogger("panopticon.telegram")

_API = "https://api.telegram.org"


def _enabled() -> bool:
    return bool(
        getattr(settings, "TELEGRAM_ENABLED", False)
        and getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        and getattr(settings, "TELEGRAM_CHAT_ID", "")
    )


async def send_telegram(text: str) -> bool:
    """
    Invia un messaggio HTML al chat configurato. Ritorna True se inviato.
    Non solleva mai: in caso di errore logga e ritorna False.
    """
    if not _enabled():
        return False
    url = f"{_API}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": text[:4000],   # limite Telegram 4096; tronco con margine
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, data=payload)
            if r.status_code != 200:
                log.warning("Telegram sendMessage HTTP %s: %s", r.status_code, r.text[:200])
                return False
            return True
    except Exception as ex:  # noqa: BLE001
        log.warning("Invio Telegram fallito: %s", ex)
        return False


# ---------- helper per i singoli eventi (parte A) ----------
async def notify_offline(site_name: str, site_url: str, reason: str = "",
                         attempts: int = 0, window_min: int = 0) -> bool:
    """Ritorna True se la notifica e' stata inviata davvero (serve al worker per
    segnare il flag solo a invio riuscito e ritentare se fallisce)."""
    if not getattr(settings, "TELEGRAM_NOTIFY_OFFLINE", True):
        return False
    # riga di conferma: rende esplicito che l'offline e' stato verificato con piu' check
    # (non un singolo buco transitorio). attempts/window_min sono passati dal worker.
    conf = ""
    if attempts > 1:
        if window_min > 0:
            conf = "\n" + t("Confermato dopo {attempts} check in ~{window_min} min", DEFAULT_LANGUAGE).format(attempts=attempts, window_min=window_min)
        else:
            conf = "\n" + t("Confermato dopo {attempts} check", DEFAULT_LANGUAGE).format(attempts=attempts)
    extra = f"\n<i>{_esc(reason)}</i>" if reason else ""
    status = t("non raggiungibile", DEFAULT_LANGUAGE)
    return await send_telegram(
        f"🔴 <b>{_esc(site_name)}</b> {status}\n{_esc(site_url)}{conf}{extra}"
    )


async def notify_online(site_name: str, site_url: str) -> bool:
    if not getattr(settings, "TELEGRAM_NOTIFY_OFFLINE", True):
        return False
    status = t("di nuovo raggiungibile", DEFAULT_LANGUAGE)
    return await send_telegram(
        f"🟢 <b>{_esc(site_name)}</b> {status}\n{_esc(site_url)}"
    )


async def notify_update_failed(site_name: str, item: str, reason: str = "") -> None:
    if not getattr(settings, "TELEGRAM_NOTIFY_FAILURES", True):
        return
    extra = f"\n<i>{_esc(reason)}</i>" if reason else ""
    label = t("update fallito", DEFAULT_LANGUAGE)
    await send_telegram(
        f"❌ <b>{_esc(site_name)}</b> — {label}: {_esc(item)}{extra}"
    )


# ---------- riepilogo ciclo (parte B) ----------
async def notify_cycle_summary(applied: int, sites_touched: int, failed: int,
                               failed_detail: list[str] | None = None,
                               ok_detail: list[str] | None = None) -> None:
    """Un solo messaggio a fine ciclo. Inviato solo se applied>0 o failed>0.

    ok_detail: lista "Sito: Plugin vecchia->nuova, ..." (una riga per sito aggiornato).
    failed_detail: lista "Sito: Plugin" dei fallimenti.
    Entrambe troncate se troppo lunghe, per non superare i limiti di Telegram.
    """
    if not getattr(settings, "TELEGRAM_NOTIFY_SUMMARY", True):
        return
    if applied == 0 and failed == 0:
        return  # niente da dire, niente spam

    MAX_ROWS = 30   # limite righe di dettaglio per non fare un papiro

    lines = [f"📊 <b>{t('Ciclo aggiornamenti completato', DEFAULT_LANGUAGE)}</b>"]
    lines.append("✅ " + t("{applied} update applicati su {sites_touched} siti", DEFAULT_LANGUAGE).format(applied=applied, sites_touched=sites_touched))

    # dettaglio dei successi: una riga per sito con i plugin e le versioni
    if ok_detail:
        shown = ok_detail[:MAX_ROWS]
        lines.append("\n".join(f"• {_esc(x)}" for x in shown))
        if len(ok_detail) > MAX_ROWS:
            lines.append(t("…e altri {count} siti", DEFAULT_LANGUAGE).format(count=len(ok_detail) - MAX_ROWS))

    if failed > 0:
        lines.append("\n❌ " + t("{failed} falliti", DEFAULT_LANGUAGE).format(failed=failed))
        if failed_detail:
            shown = failed_detail[:MAX_ROWS]
            lines.append("\n".join(f"• {_esc(x)}" for x in shown))
            if len(failed_detail) > MAX_ROWS:
                lines.append(t("…e altri {count}", DEFAULT_LANGUAGE).format(count=len(failed_detail) - MAX_ROWS))
    await send_telegram("\n".join(lines))


def _esc(s: str) -> str:
    """Escape minimale per HTML mode di Telegram."""
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# ============================================================================
# CENTRO SICUREZZA - Alert vulnerabilita'
# ============================================================================
def _sev_badge(severity: str, exploited: bool) -> str:
    """Emoji + etichetta per la gravita'. KEV (sfruttata) ha priorita' visiva massima."""
    if exploited:
        return "🚨🔴 " + t("SFRUTTATA ATTIVAMENTE", DEFAULT_LANGUAGE)
    labels = {
        "critical": "🔴 " + t("CRITICA", DEFAULT_LANGUAGE),
        "high": "🟠 " + t("ALTA", DEFAULT_LANGUAGE),
        "medium": "🟡 " + t("MEDIA", DEFAULT_LANGUAGE),
        "low": "⚪ " + t("BASSA", DEFAULT_LANGUAGE),
    }
    return labels.get(severity, "⚪ " + t("SCONOSCIUTA", DEFAULT_LANGUAGE))


async def notify_vuln_alerts(alerts: list) -> int:
    """
    Invia gli alert per le NUOVE vulnerabilita' trovate su siti non aggiornati.
    'alerts' e' la lista di dict prodotta da security.refresh_and_match():
      {site_name, site_url, ext_name, ext_version, vuln}  (vuln = oggetto Vulnerability)
    Ordina per gravita' (sfruttate in cima). Raggruppa in pochi messaggi per non floodare.
    Ritorna il numero di alert notificati.
    """
    if not _enabled() or not alerts:
        return 0

    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}

    def _rank(a):
        v = a["vuln"]
        return (1 if v.exploited_in_wild else 0, sev_rank.get(v.severity, 0), v.cvss)

    alerts = sorted(alerts, key=_rank, reverse=True)

    MAX_SINGLE = 15
    sent = 0

    for a in alerts[:MAX_SINGLE]:
        v = a["vuln"]
        badge = _sev_badge(v.severity, v.exploited_in_wild)
        cve = f"\n🔖 {_esc(v.cve_id)}" if v.cve_id else ""
        cvss = f" (CVSS {v.cvss:.1f})" if v.cvss and v.cvss > 0 else ""
        fix = (f"\n✅ <b>{t('Aggiorna a', DEFAULT_LANGUAGE)} {_esc(v.version_fixed)}</b>"
               if v.version_fixed else "\n⚠️ " + t("Nessun fix noto: valuta di disabilitare l'estensione", DEFAULT_LANGUAGE))
        title = _esc(v.title)[:300]

        msg = (
            f"{badge}{cvss}\n"
            f"🌐 <b>{_esc(a['site_name'])}</b>\n"
            f"🧩 {_esc(a['ext_name'])} <code>{_esc(a['ext_version'])}</code>"
            f"{cve}\n"
            f"📝 {title}"
            f"{fix}\n"
            f"🔗 {_esc(a['site_url'])}"
        )
        if await send_telegram(msg):
            sent += 1

    extra = len(alerts) - MAX_SINGLE
    if extra > 0:
        crit = sum(1 for a in alerts if a["vuln"].exploited_in_wild)
        await send_telegram(
            "➕ <b>" + t("Altre {count} vulnerabilità", DEFAULT_LANGUAGE).format(count=extra) + "</b> " + t("rilevate (non elencate qui).", DEFAULT_LANGUAGE) + "\n"
            + "🚨 " + t("Sfruttate attivamente in totale: {count}", DEFAULT_LANGUAGE).format(count=crit) + "\n"
            + t("Apri il Centro Sicurezza in Sentinel TD per l'elenco completo.", DEFAULT_LANGUAGE)
        )
        sent += 1

    return sent
