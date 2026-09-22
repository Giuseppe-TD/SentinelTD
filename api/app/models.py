from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(190))
    url: Mapped[str] = mapped_column(String(255))            # https://sito.it  (senza slash finale)
    cms: Mapped[str] = mapped_column(String(20))             # 'wp' | 'joomla'
    admin_url: Mapped[str] = mapped_column(String(255), default="")
    token: Mapped[str] = mapped_column(String(128))          # bearer del connettore
    group: Mapped[str] = mapped_column(String(80), default="")
    # tag/cartelle multiple: CSV normalizzato (es. "clienteA,fascia1,onprem").
    # Usato per raggruppare/filtrare siti anche fuori dal nostro server (deploy su 70+ siti).
    tags: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=180)

    # stato ultimo check (denormalizzato: con 30-100 siti va benissimo)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")   # ok | error | unknown
    error: Mapped[str] = mapped_column(Text, default="")
    # True se per l'episodio offline corrente la notifica Telegram e' GIA' partita con
    # successo. Evita doppioni e, soprattutto, permette di RITENTARE la notifica ai check
    # successivi se il primo invio e' fallito (Telegram irraggiungibile) o se il down e'
    # stato rilevato in ritardo (worker fermo al momento esatto della transizione).
    # Si azzera quando il sito torna ok.
    offline_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    # silenzia gli avvisi del singolo sito senza interrompere monitoraggio/update
    notifications_silenced: Mapped[bool] = mapped_column(Boolean, default=False)

    # scadenza dominio (RDAP/WHOIS): check settimanale, stato denormalizzato per lista/dashboard
    domain_name: Mapped[str] = mapped_column(String(255), default="")
    domain_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    domain_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    domain_check_error: Mapped[str] = mapped_column(Text, default="")
    # JSON {"expires":"YYYY-MM-DD","sent":[30,14,7]} per evitare doppioni
    domain_alert_state: Mapped[str] = mapped_column(Text, default="")
    core_current: Mapped[str] = mapped_column(String(40), default="")
    core_latest: Mapped[str] = mapped_column(String(40), default="")
    core_update: Mapped[bool] = mapped_column(Boolean, default=False)
    updates_count: Mapped[int] = mapped_column(Integer, default=0)
    # contatori per categoria: aggiornabili / totali installati
    upd_plugins: Mapped[int] = mapped_column(Integer, default=0)
    tot_plugins: Mapped[int] = mapped_column(Integer, default=0)
    upd_themes: Mapped[int] = mapped_column(Integer, default=0)
    tot_themes: Mapped[int] = mapped_column(Integer, default=0)
    upd_other: Mapped[int] = mapped_column(Integer, default=0)
    tot_other: Mapped[int] = mapped_column(Integer, default=0)
    php_version: Mapped[str] = mapped_column(String(20), default="")

    # screenshot
    shot_path: Mapped[str] = mapped_column(String(255), default="")     # path relativo dentro /data/screenshots
    shot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    extensions: Mapped[list["Extension"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", lazy="selectin"
    )


class Extension(Base):
    __tablename__ = "extensions"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30))           # plugin/theme | component/module/plugin/template/library
    name: Mapped[str] = mapped_column(String(190))
    slug: Mapped[str] = mapped_column(String(190), default="")
    current_version: Mapped[str] = mapped_column(String(40), default="")
    new_version: Mapped[str] = mapped_column(String(40), default="")
    update_available: Mapped[bool] = mapped_column(Boolean, default=False)
    # update disponibile ma non scaricabile: manca la download key/licenza (Joomla)
    dlkey_missing: Mapped[bool] = mapped_column(Boolean, default=False)

    # cooldown auto-update: se un update fallisce (es. licenza mancante) non riprovare
    # ogni ora. Si riprova solo dopo AUTOUPDATE_RETRY_DAYS o se esce una versione nuova.
    update_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    update_failed_version: Mapped[str] = mapped_column(String(40), default="")

    site: Mapped[Site] = relationship(back_populates="extensions")


class SiteExpiry(Base):
    """Registro globale di plugin/temi/licenze con rinnovo, non legato a un singolo sito."""
    __tablename__ = "site_expiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Colonna legacy mantenuta nullable per compatibilita' con installazioni esistenti.
    # Le nuove scadenze sono globali e quindi site_id resta NULL.
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(190))
    category: Mapped[str] = mapped_column(String(40), default="Licenza")
    platform: Mapped[str] = mapped_column(String(20), default="both")  # wp | joomla | both
    provider: Mapped[str] = mapped_column(String(190), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # ricorrenza in mesi: 0 = una tantum, 1 = mensile, 3 = trimestrale, 12 = annuale, N = ogni N mesi
    recur_months: Mapped[int] = mapped_column(Integer, default=0)
    alert_state: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    site: Mapped[Site | None] = relationship()


class AdminAuth(Base):
    """
    Credenziali dell'unico admin (single-user). Un solo record (id=1).
    - password_hash: bcrypt (la password NON sta piu' in chiaro nel .env)
    - totp_secret / totp_enabled: secondo fattore TOTP (Google Authenticator/Authy)
    - passkeys: lista JSON di credenziali WebAuthn registrate
    """
    __tablename__ = "admin_auth"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), default="admin")
    password_hash: Mapped[str] = mapped_column(String(255), default="")

    totp_secret: Mapped[str] = mapped_column(String(64), default="")
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # ultima "window" TOTP usata con successo (counter da pyotp.TOTP.timecode). Serve a
    # rifiutare il replay dello stesso codice entro la sua finestra di validita': se un
    # codice di 30s viene intercettato, non puo' essere riutilizzato per un secondo login.
    totp_last_window: Mapped[int] = mapped_column(Integer, default=0)

    # JSON serializzato con le credenziali passkey (cred_id, public_key, sign_count, ...)
    passkeys: Mapped[str] = mapped_column(Text, default="[]")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
# ============================================================================
# CENTRO SICUREZZA - Modelli DB
# ============================================================================
# Da AGGIUNGERE in fondo a api/app/models.py (dopo la classe AdminAuth).
# Le tabelle vengono create automaticamente da Base.metadata.create_all in
# run_migrations() al boot: non serve scrivere ALTER manuali.
#
# Due tabelle:
#  - vulnerabilities: le vulnerabilita' scaricate dalle fonti (KEV, VEL, NVD, wporg).
#    Una riga per (fonte, identificativo vuln, estensione+cms colpita).
#  - vuln_matches: l'incrocio tra una vulnerabilita' e un sito che la espone.
#    Una riga per (sito, estensione, vulnerabilita'). Tiene lo stato: vulnerabile
#    o risolto, quando visto la prima volta, se gia' notificato via Telegram.
# ============================================================================


class Vulnerability(Base):
    """
    Una vulnerabilita' nota, come arriva da una delle fonti aggregate.
    Il match con i siti avviene su (affected_slug + cms), confrontando la versione
    installata con version_fixed.
    """
    __tablename__ = "vulnerabilities"

    id: Mapped[int] = mapped_column(primary_key=True)

    # fonte del dato: 'kev' (CISA) | 'vel' (Joomla) | 'nvd' | 'wporg'
    source: Mapped[str] = mapped_column(String(16), index=True)

    # identificativo: CVE quando c'e', altrimenti id interno della fonte (es. VEL id)
    cve_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    ext_ref: Mapped[str] = mapped_column(String(64), default="")   # id nella fonte (VEL id, GHSA, ecc.)

    title: Mapped[str] = mapped_column(String(500), default="")

    # a chi si applica: slug normalizzato dell'estensione + tipo + cms
    # per Joomla lo slug e' l'element (com_baforms, plg_system_xxx)
    # per WP lo slug e' la cartella del plugin/tema (elementor, advanced-custom-fields)
    affected_slug: Mapped[str] = mapped_column(String(190), default="", index=True)
    affected_type: Mapped[str] = mapped_column(String(30), default="")   # component/plugin/theme/... o '' se generico
    cms: Mapped[str] = mapped_column(String(20), default="", index=True)  # 'joomla' | 'wp' | '' (generico/core)

    # versione in cui e' stata corretta (per confronto). Vuoto = nessun fix noto (LIVE VEL).
    version_fixed: Mapped[str] = mapped_column(String(40), default="")

    # gravita'
    severity: Mapped[str] = mapped_column(String(16), default="")    # critical/high/medium/low/unknown
    cvss: Mapped[float] = mapped_column(default=0.0)                 # 0..10
    exploited_in_wild: Mapped[bool] = mapped_column(Boolean, default=False)  # True se in CISA KEV

    url: Mapped[str] = mapped_column(String(500), default="")        # link al bollettino/notice
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VulnMatch(Base):
    """
    Un match tra una vulnerabilita' e un sito che la espone (estensione installata,
    versione sotto version_fixed). Uno stato per coppia (sito, estensione, vuln).
    """
    __tablename__ = "vuln_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    extension_id: Mapped[int | None] = mapped_column(
        ForeignKey("extensions.id", ondelete="SET NULL"), nullable=True
    )
    vulnerability_id: Mapped[int] = mapped_column(
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"), index=True
    )

    site_version: Mapped[str] = mapped_column(String(40), default="")   # versione installata al momento del match
    is_vulnerable: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)      # alert Telegram gia' inviato

    site: Mapped["Site"] = relationship()
    vulnerability: Mapped["Vulnerability"] = relationship()


class AppSetting(Base):
    """Impostazioni chiave/valore dell'applicazione (es. chiave di registrazione agent)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class UpdateHistory(Base):
    """Storico degli update applicati/falliti (conservato 7 giorni, pulizia nel tick).
    Alimenta la timeline nel dettaglio sito e la dashboard di Sentinel."""
    __tablename__ = "update_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(Integer, index=True)
    site_name: Mapped[str] = mapped_column(String(255), default="")
    cms: Mapped[str] = mapped_column(String(16), default="")
    ext_type: Mapped[str] = mapped_column(String(32), default="")
    ext_name: Mapped[str] = mapped_column(String(255), default="")
    slug: Mapped[str] = mapped_column(String(255), default="")
    from_version: Mapped[str] = mapped_column(String(64), default="")
    to_version: Mapped[str] = mapped_column(String(64), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class UpdateMonthly(Base):
    """Rollup mensile degli update: una riga per (periodo, sito, estensione).

    Lo storico dettagliato (UpdateHistory) resta a 7 giorni; questo aggregato e'
    minuscolo e viene conservato per sempre, cosi' il report mensile puo' dire
    "su questo sito il plugin X e' stato aggiornato 3 volte" anche a distanza di anni.
    """
    __tablename__ = "update_monthly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(7), index=True)          # YYYY-MM
    site_id: Mapped[int] = mapped_column(Integer, index=True)
    site_name: Mapped[str] = mapped_column(String(255), default="")
    cms: Mapped[str] = mapped_column(String(16), default="")
    ext_type: Mapped[str] = mapped_column(String(32), default="")
    ext_name: Mapped[str] = mapped_column(String(255), default="")
    slug: Mapped[str] = mapped_column(String(255), default="")
    ok_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_version: Mapped[str] = mapped_column(String(64), default="")
    # versione PRIMA del primo aggiornamento del mese: con last_version da' il "dalla X alla Y"
    first_version: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
