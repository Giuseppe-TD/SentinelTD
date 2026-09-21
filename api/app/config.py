from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DEFAULT_UI_LANGUAGE: str = "en"

    @field_validator("JWT_SECRET")
    @classmethod
    def valid_session_key(cls, value: str) -> str:
        if len(value.strip()) < 32 or value.lower().startswith(("change", "cambiami")):
            raise ValueError("JWT_SECRET must be a new random secret of at least 32 characters")
        return value

    @field_validator("ADMIN_PASSWORD")
    @classmethod
    def valid_admin_password(cls, value: str) -> str:
        if len(value) < 12 or value.lower() in ("changeme12345", "cambiami12345"):
            raise ValueError("ADMIN_PASSWORD must contain at least 12 characters")
        return value

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"

    ADMIN_PASSWORD: str
    JWT_SECRET: str
    JWT_HOURS: int = 12

    # --- 2FA ---
    # nome mostrato nell'app Authenticator (TOTP)
    TOTP_ISSUER: str = "Panopticon"
    # passkey/WebAuthn: dominio (rpId) e origin del sito Panopticon.
    # rpId = solo dominio senza schema (es. app.example.com)
    # origin = url completo (es. https://app.example.com)
    WEBAUTHN_RP_ID: str = ""
    WEBAUTHN_RP_NAME: str = "Panopticon"
    WEBAUTHN_ORIGIN: str = ""

    SHOOTER_URL: str = "http://shooter:8090"
    SCHEDULER_TICK_MINUTES: int = 5
    SCREENSHOT_EVERY_HOURS: int = 12

    # --- Conferma offline (debounce) ---
    # Prima di marcare un sito "offline" lo si ricontrolla piu' volte a distanza, cosi'
    # un singolo buco transitorio (WAF/CrowdSec, 502, timeout, risposta cachata dal proxy)
    # non genera falsi offline ne' notifiche Telegram inutili. Il debounce scatta SOLO
    # sulla transizione ok -> error: se il sito era gia' offline si comporta come prima.
    OFFLINE_CONFIRM_CHECKS: int = 3          # check totali prima di dichiarare offline (1 = nessun retry)
    OFFLINE_RETRY_DELAY_SECONDS: int = 120   # attesa tra un check e l'altro

    # --- Auto-update schedulato ---
    AUTOUPDATE_ENABLED: bool = False     # interruttore globale
    AUTOUPDATE_HOUR: int = 3             # ora notturna in cui parte il ciclo
    AUTOUPDATE_PAUSE_SECONDS: int = 5    # pausa tra un update e l'altro sullo stesso sito
    AUTOUPDATE_SITE_STAGGER_SECONDS: int = 30  # ritardo tra un sito e il successivo
    AUTOUPDATE_RETRY_HOURS: int = 24     # ore di attesa prima di riprovare un update fallito (1 = riprova al ciclo dell'ora dopo)

    # --- SMTP report (compila nel .env) ---
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""
    SMTP_FROM_NAME: str = "Sentinel TD"   # nome mittente leggibile
    REPORT_TO: str = ""

    # --- Notifiche Telegram ---
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    # interruttori fini per categoria di notifica
    TELEGRAM_NOTIFY_FAILURES: bool = True    # update falliti (immediato)
    TELEGRAM_NOTIFY_OFFLINE: bool = True     # sito offline / tornato online (immediato)
    TELEGRAM_NOTIFY_SUMMARY: bool = True     # riepilogo a fine ciclo auto-update

    # --- Centro Sicurezza ---
    # Feed CISA KEV (mirror GitHub per evitare i rate-limit di cisa.gov).
    KEV_FEED_URL: str = "https://raw.githubusercontent.com/BenjiTrapp/cisa-known-vuln-scraper/main/cisa-kev.json"
    # VEL Joomla (feed + hash di verifica per non riscaricare se invariato).
    VEL_FEED_URL: str = "https://extensions.joomla.org/vel-feed"
    VEL_VERIFY_URL: str = "https://extensions.joomla.org/vel-verify"
    # NVD: DISATTIVATO di default. Il match per keyword genera falsi positivi a valanga
    # (CVE di prodotti diversi che condividono parole nel titolo) ed e' lento senza API key.
    # Le fonti affidabili sono VEL (Joomla, slug preciso) e wordpress.org (WP). KEV serve
    # solo a marcare "sfruttata attivamente" le vuln gia' trovate. Lascia false salvo test.
    SECURITY_USE_NVD: bool = False
    SECURITY_NVD_MAX_KEYWORDS: int = 40      # tetto keyword NVD per giro (rate-limit)
    # dove salvare lo stato leggero (hash VEL). Volatile va bene: se sparisce, riscarica.
    SECURITY_STATE_DIR: str = "/tmp/panopticon-sec"


settings = Settings()
