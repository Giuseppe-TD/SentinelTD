from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=5)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# advisory lock condiviso: serializza create_all/ALTER tra i vari worker/processi al boot
_MIGRATION_LOCK = 841020260529


async def run_migrations(conn) -> None:
    """
    Crea le tabelle mancanti e applica gli ALTER idempotenti (ADD COLUMN IF NOT EXISTS).
    Eseguita SIA dall'API (lifespan) SIA dal worker (on_startup), cosi' i due container
    sono autonomi: chi parte per primo migra, gli altri rieseguono gli ALTER (no-op).
    Va invocata dentro `async with engine.begin() as conn:`.
    """
    from . import models  # noqa: F401  (registra i modelli per create_all)

    # serializza i processi concorrenti (gunicorn -w 2 + worker) sul boot
    await conn.execute(text(f"SELECT pg_advisory_xact_lock({_MIGRATION_LOCK})"))
    await conn.run_sync(Base.metadata.create_all)

    # contatori per categoria
    for col in ("upd_plugins", "tot_plugins", "upd_themes", "tot_themes", "upd_other", "tot_other"):
        await conn.execute(text(f"ALTER TABLE sites ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL DEFAULT 0"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS auto_update BOOLEAN NOT NULL DEFAULT FALSE"))
    # tag/cartelle multiple sui siti (CSV)
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT ''"))
    # flag: notifica offline gia' inviata con successo per l'episodio corrente
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS offline_notified BOOLEAN NOT NULL DEFAULT FALSE"))
    # silenziamento notifiche per sito + scadenza dominio WHOIS/RDAP
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS notifications_silenced BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_name VARCHAR(255) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_expires_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_checked_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_check_error TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_alert_state TEXT NOT NULL DEFAULT ''"))
    # registro globale plugin/temi/licenze: sganciato dai singoli siti.
    await conn.execute(text("ALTER TABLE site_expiries ADD COLUMN IF NOT EXISTS category VARCHAR(40) NOT NULL DEFAULT 'Licenza'"))
    await conn.execute(text("ALTER TABLE site_expiries ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'both'"))
    await conn.execute(text("ALTER TABLE site_expiries ALTER COLUMN site_id DROP NOT NULL"))
    await conn.execute(text("ALTER TABLE site_expiries ADD COLUMN IF NOT EXISTS recur_months INTEGER NOT NULL DEFAULT 0"))

    # Rollup mensile per i report: chiave unica (periodo, sito, tipo, slug) per l'upsert.
    await conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_update_monthly_key
        ON update_monthly (period, site_id, ext_type, slug)
    """))
    # Versione di partenza nel rollup (per il report dettagliato "dalla X alla Y").
    await conn.execute(text("ALTER TABLE update_monthly ADD COLUMN IF NOT EXISTS first_version VARCHAR(64) NOT NULL DEFAULT ''"))
    done_fv = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = 'migr:monthly_first_version_done'"
    ))).scalar()
    if not done_fv:
        # recupera la versione di partenza dallo storico ancora disponibile: per ogni
        # (mese, sito, estensione) la from_version del PRIMO aggiornamento del mese
        await conn.execute(text("""
            UPDATE update_monthly m SET first_version = h.from_version
            FROM (
                SELECT DISTINCT ON (to_char(created_at, 'YYYY-MM'), site_id, ext_type, slug)
                       to_char(created_at, 'YYYY-MM') AS period, site_id, ext_type, slug, from_version
                FROM update_history
                WHERE from_version <> ''
                ORDER BY to_char(created_at, 'YYYY-MM'), site_id, ext_type, slug, created_at ASC
            ) h
            WHERE m.period = h.period AND m.site_id = h.site_id AND m.ext_type = h.ext_type
              AND m.slug = h.slug AND m.first_version = ''
        """))
        await conn.execute(text(
            "INSERT INTO app_settings (key, value) VALUES ('migr:monthly_first_version_done', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = '1'"
        ))

    # Backfill una tantum dallo storico ancora presente (max 7 giorni): evita un report
    # vuoto se il primo invio cade poco dopo l'aggiornamento del sistema.
    done_roll = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = 'migr:monthly_backfill_done'"
    ))).scalar()
    if not done_roll:
        await conn.execute(text("""
            INSERT INTO update_monthly (period, site_id, site_name, cms, ext_type, ext_name, slug, ok_count, fail_count, last_version)
            SELECT to_char(created_at, 'YYYY-MM'), site_id, max(site_name), max(cms), ext_type, max(ext_name), slug,
                   count(*) FILTER (WHERE ok), count(*) FILTER (WHERE NOT ok), max(to_version)
            FROM update_history
            GROUP BY to_char(created_at, 'YYYY-MM'), site_id, ext_type, slug
            ON CONFLICT (period, site_id, ext_type, slug) DO NOTHING
        """))
        await conn.execute(text(
            "INSERT INTO app_settings (key, value) VALUES ('migr:monthly_backfill_done', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = '1'"
        ))
    # Migrazione DAVVERO una tantum dei record legacy (eredita il CMS del sito, poi
    # diventa globale). Protetta da un flag in app_settings: senza, queste UPDATE
    # girerebbero a OGNI avvio e cancellerebbero qualsiasi futura associazione
    # scadenza->sito al primo riavvio.
    done = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = 'migr:expiries_global_done'"
    ))).scalar()
    if not done:
        await conn.execute(text("""
            UPDATE site_expiries e
            SET platform = CASE WHEN lower(s.cms) IN ('wp','wordpress') THEN 'wp' ELSE 'joomla' END
            FROM sites s
            WHERE e.site_id = s.id AND (e.platform IS NULL OR e.platform = '' OR e.platform = 'both')
        """))
        await conn.execute(text("UPDATE site_expiries SET site_id = NULL WHERE site_id IS NOT NULL"))
        await conn.execute(text(
            "INSERT INTO app_settings (key, value) VALUES ('migr:expiries_global_done', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = '1'"
        ))

    # estensioni: cooldown fallimento update
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS update_failed_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS update_failed_version VARCHAR(40) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS dlkey_missing BOOLEAN NOT NULL DEFAULT FALSE"))

    # admin_auth: 2FA / passkey
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS passkeys TEXT NOT NULL DEFAULT '[]'"))
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS totp_last_window BIGINT NOT NULL DEFAULT 0"))


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
