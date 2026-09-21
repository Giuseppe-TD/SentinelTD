# Technical analysis and verification

## Application structure

The application is a central WordPress/Joomla monitoring hub. `api/app/main.py` exposes FastAPI routes; async SQLAlchemy models store monitored sites, update history, preferences, security matches and reporting state in PostgreSQL. `api/app/worker.py` runs the arq/Redis background tasks. `shooter/` is a separate Playwright service for website captures. PDF reports use WeasyPrint. The main Alpine interface is `api/static/sentinel/index.html`; older static dashboard/security pages remain included.

Monitoring, CMS update requests, security feeds, domain/license expiry tracking, notifications, report templates, MFA/passkeys and branding were inspected to identify user-visible text, environment settings and persistent storage. This is a source review for localization and release preparation, not a penetration test.

## Release changes

- Added local Italian/English/French/German catalogs, shared translation runtime, selector, browser detection and persisted per-application language preference across included pages. Labels added after page load are translated. Dates use the selected locale when rendered.
- Localized built-in report and notification templates before user values are inserted. Outgoing defaults use `DEFAULT_UI_LANGUAGE`; custom database templates retain their content.
- Removed private environment files, backup configuration and Python caches. The archive did not contain a database dump/file; existing server volumes are outside the archive and have not been touched.
- Added blank-secret environment examples, secret validation, release ignores and explicit service settings. Corrected screenshot environment names to those read by the screenshot implementation.
- Kept API access on loopback by default, with configurable host binding. Database, Redis and screenshot service are internal. Database startup readiness is checked before starting dependent application services.
- Added English setup, maintenance, troubleshooting, language and GitHub publishing documentation, plus repeatable catalog/source checks.

## Important integration details

The WordPress/Joomla connector packages described by the original project are **not included in the supplied archive**. The hub's connector upload/download and registration functions remain, but CMS integration cannot be completed until compatible connector packages are supplied separately. A clean database also has no registered sites or site tokens.

The screenshot service serializes captures with its existing lock. Changing a nonexistent concurrency environment variable cannot increase throughput. Monitoring and update operations require the target site's connector credentials and network access. Security-feed matches indicate known findings, not proof that a website is secure. Per-site content, third-party findings and administrator-authored text are not machine-translated.

## Verification

- Parsed all application Python files and checked non-vendor JavaScript syntax.
- Checked matching catalog keys, placeholder preservation, deterministic bundle generation, regional browser language detection and fallback behavior.
- Exercised login and dashboard pages in headless Edge using local files and simulated API responses: four language choices, persisted selection, dynamic labels and mobile selector layout. No uncaught browser errors in these tested flows.
- Scanned the delivered source against credential values from the original environment files; no matches. Checked for database/runtime files and nonempty example secrets.
- Tested the separate language patch on disposable copies: compatible application, repeat application, rollback, refusal to overwrite customized files, and preservation of dummy environment/database files.

Docker is unavailable on the preparation machine. Container builds, a real PostgreSQL/Redis stack, migrations against a live database, CMS updates, passkey enrollment, SMTP/Telegram delivery, screenshot captures and PDF rendering were not executed. Validate those integrations on a fresh test deployment before production use.

Run `node --test tests/i18n.test.cjs` and `python tests/check_release.py` in a clean source checkout. The latter is intentionally a release check: a local `.env` or runtime database makes it fail. The GitHub validation workflow runs these checks on committed source.
