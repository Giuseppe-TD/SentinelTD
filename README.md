<div align="center">

# Sentinel TD

**Self-hosted monitoring, maintenance and security dashboard for WordPress and Joomla websites.**

![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Languages](https://img.shields.io/badge/UI-EN%20%7C%20IT%20%7C%20FR%20%7C%20DE-6C63FF)
![Licence](https://img.shields.io/badge/licence-AGPL--3.0-750014)

Monitor websites, manage updates, track renewals, review security findings and receive automated notifications from one dashboard.

</div>

---

## Overview

Sentinel TD is a Docker-based control panel designed to centralize the day-to-day management of WordPress and Joomla installations.

It combines website availability monitoring, CMS and extension update tracking, screenshot capture, security checks, domain and license expiry management, email/Telegram notifications and scheduled reports in a single self-hosted interface.

### Key features

- Website availability and status monitoring.
- WordPress and Joomla version/update tracking through the included connectors.
- Manual, selected and bulk update workflows.
- Optional scheduled/nightly update cycles with automatic retries.
- Guided bulk install/remove across many sites: select by folder, and removal targets only the sites that actually have the extension.
- Isolated browser screenshots (Google Chrome, so H.264 background videos render) with list thumbnails.
- Security and vulnerability feed aggregation.
- Domain expiry monitoring with configurable reminders.
- Plugin, theme and license renewal tracking with recurring renewals.
- Tags, folders, CSV export and update history.
- Email and Telegram notifications with editable templates.
- Monthly PDF reports, per-folder or global, sent automatically.
- On-demand detailed reports (PDF or CSV): per site and component, how many times it was updated and from which version to which, with the full history of every update.
- Statistics dashboard with month-over-month comparison.
- Password authentication, TOTP and passkeys.
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

> **CMS connectors:** the WordPress and Joomla connectors ship with the application — their sources
> are in `connectors/` and contain no panel address and no key. Set *Settings → Connectors → Public
> address of this panel*, then press **Download**: Sentinel TD builds the package from those sources
> and writes your address and registration key into the WordPress one, so new sites connect
> themselves. Sites can also be configured by hand from their own admin page, and
> `python scripts/build_connectors.py` builds the packages outside the panel —
> see `connectors/README.md`.

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

The default host binding is loopback. If your reverse proxy runs on another host or in another container, configure `BIND_ADDRESS` and network routing deliberately instead of pointing the proxy to its own `localhost`. For example, with the proxy on another machine of the same LAN:

```env
BIND_ADDRESS=0.0.0.0   # then restrict port 8810 to the proxy's address with a firewall
TZ=Europe/Rome         # scheduled updates, monthly reports and month boundaries follow this zone
DEFAULT_UI_LANGUAGE=it # language of server-generated emails and PDF reports
```

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

- `CHANGELOG.md` — release notes.
- `connectors/README.md` — WordPress/Joomla connectors and how to build them.
- `docs/LANGUAGES.md` — language maintenance.
- `docs/PUBLISHING.md` — GitHub publishing guide.
- `docs/ANALYSIS.md` — analysis and verification notes.
- `SECURITY.md` — security information.

## Support this project

Sentinel TD is developed and maintained in the open. If it saves you time, a donation helps keep it
going — the **Sponsor** button on this repository points to the current donation options.

Contributions are welcome too: bug reports with clear reproduction steps, translations and
documentation fixes are as valuable as code.

## Licence

Sentinel TD is free software released under the **GNU Affero General Public License v3.0 or later**
(AGPL-3.0-or-later). You may use, study, modify and redistribute it; if you distribute a modified
version, or run one as a network service for other people, the corresponding source must be made
available under the same licence. The full text is in [`LICENSE`](LICENSE).

The bundled connectors are covered by the same licence and are compatible with the WordPress and
Joomla ecosystems (GPL-2.0-or-later). Dependency and bundled asset licences remain applicable.

Copyright © 2026 **Giuseppe Sciarra / Tastiere Digitali**.
