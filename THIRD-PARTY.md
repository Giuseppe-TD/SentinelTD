# Third-party components

Sentinel TD is released under AGPL-3.0-or-later. It uses the following third-party
components, each under its own license.

## Panel and worker (Python, installed by pip)

| Component | License |
|-----------|---------|
| [FastAPI](https://fastapi.tiangolo.com) / [Starlette](https://www.starlette.io) | MIT / BSD-3-Clause |
| [Uvicorn](https://www.uvicorn.org) / [Gunicorn](https://gunicorn.org) | BSD-3-Clause / MIT |
| [SQLAlchemy](https://www.sqlalchemy.org) / [asyncpg](https://github.com/MagicStack/asyncpg) | MIT / Apache-2.0 |
| [arq](https://github.com/samuelcolvin/arq) / [redis-py](https://github.com/redis/redis-py) | MIT |
| [HTTPX](https://www.python-httpx.org) | BSD-3-Clause |
| [Pydantic](https://docs.pydantic.dev) / pydantic-settings | MIT |
| [python-jose](https://github.com/mpdavis/python-jose) | MIT |
| [aiosmtplib](https://github.com/cole/aiosmtplib) | MIT |
| [bcrypt](https://github.com/pyca/bcrypt) | Apache-2.0 |
| [PyOTP](https://github.com/pyauth/pyotp) / [qrcode](https://github.com/lincolnloop/python-qrcode) | MIT / BSD-3-Clause |
| [py_webauthn](https://github.com/duo-labs/py_webauthn) | BSD-3-Clause |
| [SlowAPI](https://github.com/laurentS/slowapi) | MIT |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [Jinja2](https://jinja.palletsprojects.com) | BSD-3-Clause |
| [WeasyPrint](https://weasyprint.org) — monthly and detailed PDF reports | BSD-3-Clause |

## Screenshot service

| Component | License |
|-----------|---------|
| [Playwright for Python](https://playwright.dev/python/) | Apache-2.0 |
| [Google Chrome](https://www.google.com/chrome/), installed by Playwright at build time — needed for H.264 background videos | Google Chrome Terms of Service (proprietary, not redistributed by this project) |
| [Pillow](https://python-pillow.org) — list thumbnails | MIT-CMU |

Chromium ships with Playwright and is used as a fallback when Chrome is unavailable
([BSD-3-Clause and others](https://chromium.googlesource.com/chromium/src/+/main/LICENSE)).

## Infrastructure images

| Component | License |
|-----------|---------|
| [PostgreSQL](https://www.postgresql.org) 16 (`postgres:16-alpine`) | PostgreSQL License |
| [Redis](https://redis.io) 7 (`redis:7-alpine`) | BSD-3-Clause (Redis 7.x) |
| [Python](https://www.python.org) 3.12 base image | PSF License |

## Bundled assets

| Asset | License |
|-------|---------|
| [Alpine.js](https://alpinejs.dev) 3.14.8 (`api/static/vendor/alpine.min.js`) | MIT |
| [Inter](https://rsms.me/inter/) font (`api/static/vendor/fonts`, `inter.css`) | SIL Open Font License 1.1 |
| Panel interface, icons and stylesheets | part of Sentinel TD, AGPL-3.0 |

Both are served from the application itself: the panel makes no requests to external CDNs.

## Connectors

The WordPress and Joomla connectors in `connectors/` are part of Sentinel TD and are
released under AGPL-3.0-or-later. They run inside WordPress and Joomla, which are
themselves GPL-2.0-or-later.

## Data sources

Vulnerability information is retrieved at runtime from public feeds (such as the NVD API
and the WordPress.org and Joomla extension directories); domain expiry uses public RDAP
and WHOIS services. Those services are not redistributed with this project and remain
subject to their own terms.
