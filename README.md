<div align="center">

# Sentinel TD

**Self-hosted monitoring, maintenance and security dashboard for WordPress and Joomla websites.**

![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Languages](https://img.shields.io/badge/UI-EN%20%7C%20IT%20%7C%20FR%20%7C%20DE-6C63FF)

Monitor websites, manage updates, track renewals, review security findings and receive automated notifications from one dashboard.

</div>

---

## Overview

Sentinel TD is a Docker-based control panel designed to centralize the day-to-day management of WordPress and Joomla installations.

It combines website availability monitoring, CMS and extension update tracking, screenshot capture, security checks, domain and license expiry management, email/Telegram notifications and scheduled reports in a single self-hosted interface.

### Key features

- Website availability and status monitoring.
- WordPress and Joomla version/update tracking through compatible connectors.
- Manual, selected and bulk update workflows.
- Optional scheduled/nightly update cycles.
- Isolated browser screenshots with history.
- Security and vulnerability feed aggregation.
- Domain expiry monitoring with configurable reminders.
- Plugin, theme and license renewal tracking.
- Tags, folders, CSV export and update history.
- Email and Telegram notifications.
- Monthly PDF reports and statistics.
- Password authentication, TOTP and passkeys.
- Editable notification templates.
- Custom branding.
- English, Italian, French and German interface.

## Architecture

| Service | Purpose | Persistence |
|---|---|---|
| `api` | FastAPI application and web dashboard | Connector archive and branding |
| `worker` | Background jobs, scheduled checks, updates and reports | PostgreSQL and shared branding |
| `postgres` | PostgreSQL 16 database | `pg_data` |
| `redis` | Queue and cache | Runtime only |
| `shooter` | Playwright/Chromium screenshot service | `screenshots` |

The API is exposed on container port `8080` and, by default, on host port `8810` through Docker Compose.

> **CMS connectors:** compatible WordPress/Joomla connectors are required to connect real websites. Connector packages are not bundled in this repository.

## Requirements

- Linux server.
- Docker Engine.
- Docker Compose plugin.
- Internet access for image and dependency downloads.
- HTTPS hostname and reverse proxy for production use.
- Enough RAM for Chromium/PDF rendering and the number of monitored sites.

## Quick start

Clone the repository and enter the project directory:

```sh
git clone <your-repository-url>
cd panopticon-lite
```

Create the environment file:

```sh
cp .env.example .env
```

Configure at least:

```env
POSTGRES_PASSWORD=...
JWT_SECRET=...
ADMIN_PASSWORD=...
TZ=Europe/Rome
```

`ADMIN_PASSWORD` must contain at least 12 characters in this release.

Generate strong random values when needed:

```sh
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_hex(32))"
```

Then build and start the stack:

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Check the logs:

```sh
docker compose logs --tail=100 api worker
```

For a local check, open:

```text
http://localhost:8810
```

Health endpoint:

```text
http://localhost:8810/healthz
```

On a fresh database, sign in as `admin` using the configured `ADMIN_PASSWORD`. The initial password is used to bootstrap the administrator; subsequent password changes are stored in PostgreSQL.

## Production setup

Use an HTTPS reverse proxy in front of Sentinel TD.

The default host binding is loopback. If your reverse proxy runs on another host or in another container, configure `BIND_ADDRESS` and network routing deliberately instead of pointing the proxy to its own `localhost`.

For passkeys, configure:

```env
WEBAUTHN_RP_ID=sentinel.example.com
WEBAUTHN_ORIGIN=https://sentinel.example.com
```

`WEBAUTHN_RP_ID` must be the bare hostname. `WEBAUTHN_ORIGIN` must match the exact HTTPS origin.

## Configuration

All supported environment variables are documented in `.env.example`.

Important options include:

- `AUTOUPDATE_ENABLED` — enables or disables scheduled automatic updates.
- `OFFLINE_CONFIRM_CHECKS` — number of checks used to confirm an outage.
- `OFFLINE_RETRY_DELAY_SECONDS` — retry delay for transient outages.
- `SHOT_*` — screenshot service options.
- `SMTP_*` — outgoing email configuration.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` — Telegram notifications.
- `DEFAULT_UI_LANGUAGE` — language used by server-generated default notification/report templates: `en`, `it`, `fr` or `de`.

The language selected in the browser is independent from `DEFAULT_UI_LANGUAGE`.

Never commit your real `.env` file.

## Interface languages

Sentinel TD includes:

- English
- Italian
- French
- German

Use the language selector in the top navigation bar. **Auto · Browser** detects the browser language and falls back to English.

The selected language is stored locally in the browser.

See `docs/LANGUAGES.md` for translation maintenance details.

## Email and Telegram notifications

Sentinel TD can send operational notifications, update results, offline/online events, security information and scheduled reports through email and Telegram.

Configure SMTP and Telegram credentials in `.env`, then customize the notification templates from the application where supported.

Server-owned default templates follow `DEFAULT_UI_LANGUAGE`. Previously saved custom templates are preserved during updates.

## Updates

Back up the installation before upgrading, then run:

```sh
git pull --ff-only
docker compose up -d --build
docker compose ps
```

If you update files manually instead of using Git, replace the changed project files and rebuild the affected services. Changes under the shared `api` build context normally require rebuilding both `api` and `worker`:

```sh
docker compose up -d --build api worker
```

Schema setup and migrations run at startup.

## Backups

Back up together:

- PostgreSQL data.
- `branding` volume/data.
- `connectors` volume/data.
- `screenshots` volume/data.
- Your private `.env`.

Do **not** run:

```sh
docker compose down -v
```

unless you intentionally want to remove named volumes and their data.

A normal `docker compose down` preserves named volumes.

## Troubleshooting

**Application does not start**  
Check required secrets and inspect `api`, `worker` and PostgreSQL logs.

**No monitoring data**  
Verify the CMS connector, site token, HTTPS reachability and forwarded authorization headers.

**Screenshots are missing**  
Inspect `shooter` and `worker` logs and confirm outbound website access.

**Passkeys fail**  
Verify HTTPS, RP hostname, origin and browser support.

**Old interface text remains after an update**  
Rebuild the API/worker images and perform a hard refresh in the browser.

## Security notes

- Keep `.env` private.
- Use independent secrets for database, JWT and administrator credentials.
- Review backup and rollback procedures before enabling automatic updates.
- Do not expose PostgreSQL, Redis or the screenshot service publicly.
- Security feed matching is useful operational information, not proof that a website is vulnerability-free.

See `SECURITY.md` and `docs/ANALYSIS.md` for additional notes.

## Project documentation

- `docs/LANGUAGES.md` — language maintenance.
- `docs/PUBLISHING.md` — GitHub publishing guide.
- `docs/ANALYSIS.md` — analysis and verification notes.
- `SECURITY.md` — security information.

## ❤️ Support the project

Sentinel TD is developed and maintained independently.

If you find it useful and would like to support its continued development, you can make a contribution via PayPal.

[**Support Sentinel TD via PayPal**](https://paypal.me/raxiel87)

Every contribution helps with development, testing and maintenance.
Thank you for supporting the project.

## Attribution and licensing

Original project attribution: **Giuseppe Sciarra / Tastiere Digitali**.

No project license was supplied in the source archive and no new license grant is implied by this README. Dependency and bundled asset licenses remain applicable.
