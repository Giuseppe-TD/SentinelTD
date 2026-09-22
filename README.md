<div align="center">

# TiDiTalk

**Self-hosted WebRTC video meetings with scheduling, guest invitations and host-controlled rooms.**

![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-22-339933?logo=node.js&logoColor=white)
![WebRTC](https://img.shields.io/badge/WebRTC-mediasoup-333333)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Languages](https://img.shields.io/badge/UI-EN%20%7C%20IT%20%7C%20FR%20%7C%20DE-6C63FF)

Private video meetings, screen sharing, scheduling and invitations on infrastructure you control.

</div>

---

## Overview

TiDiTalk is a self-hosted video meeting platform built around a mediasoup WebRTC SFU.

It provides host accounts, guest links, a waiting lobby, scheduled meetings, email/calendar invitations, screen sharing, chat, reactions, annotations, local recording and configurable branding without depending on a third-party meeting platform for the core service.

### Key features

- Host accounts and guest invitation links.
- Waiting lobby when no host is present.
- Audio and video meetings.
- Screen sharing.
- Shared annotations and drawing tools.
- Chat and reactions.
- Local recording.
- Virtual backgrounds and face effects.
- Scheduled meetings.
- Email invitations and calendar attachments.
- Admin and host roles.
- User management.
- Configurable branding and room rules.
- English, Italian, French and German interface.
- Localized meeting invitation emails.

## Architecture

| Component | Purpose |
|---|---|
| Node.js 22 | Application runtime |
| Express | HTTP application server |
| Socket.IO | Signaling and realtime events |
| mediasoup | WebRTC SFU |
| SQLite | Users, meetings and application settings |
| coturn | STUN/TURN connectivity |
| esbuild | Browser bundle build |

The Docker image builds the browser mediasoup client bundle from the committed lockfile using `npm ci` and esbuild.

## Requirements

Deploy TiDiTalk on a **Linux Docker host** with:

- Docker Engine.
- Docker Compose plugin.
- A reachable public IP address.
- HTTPS hostname.
- Reverse proxy with WebSocket support.
- Required WebRTC/TURN ports forwarded through the firewall/NAT.

The bundled coturn configuration uses host networking. Do not assume Docker Desktop on Windows or macOS has identical networking behavior.

## Quick start

Clone the repository and enter the project directory:

```sh
git clone <your-repository-url>
cd videochat
```

Create the environment file:

```sh
cp .env.example .env
```

Configure at least:

```env
SERVER_SECRET=...
USERS=admin:YOUR_PASSWORD:Administrator:admin
BASE_URL=https://meet.example.com
ANNOUNCED_IP=YOUR_PUBLIC_IP
TURN_HOST=turn.example.com
TURN_REALM=example.com
TURN_USER=...
TURN_PASSWORD=...
TURN_EXTERNAL_IP=...
TURN_RELAY_IP=...
CORS_ORIGINS=https://meet.example.com
```

Generate strong random values when needed:

```sh
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_hex(32))"
```

`SERVER_SECRET` should contain at least 32 characters.

Use a long initial administrator password without `:` or `,`, because those characters are separators in the `USERS` value.

Start the stack:

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Check the logs:

```sh
docker compose logs --tail=100 app coturn
```

Open the configured `BASE_URL` and sign in with the bootstrap administrator.

## Network and firewall

| Traffic | Default | Routing |
|---|---|---|
| HTTPS / WSS | TCP 443 | Reverse proxy → application |
| Application HTTP | TCP 3010 | Loopback by default |
| mediasoup RTP | UDP + TCP 40000–40400 | Directly to the Docker host |
| STUN/TURN | UDP + TCP 3478 | To coturn host |
| TURN relay | UDP 49152–49200 | To coturn host |

Keep firewall/NAT forwarding aligned with the configured port ranges.

HTTPS alone does not transport WebRTC media or TURN relay traffic.

If the reverse proxy runs in another container or on another host, configure `BIND_ADDRESS` and routing deliberately. Its `localhost` is not the TiDiTalk host.

## Initial users

`USERS_MODE=create` creates missing accounts without replacing existing database users.

After verifying that the administrator exists, you can clear `USERS` and manage accounts from the interface.

`USERS_MODE=sync` makes `.env` authoritative at every startup; accounts managed that way are read-only in the UI.

There are no built-in default accounts in a clean installation.

## Email and meeting invitations

Configure SMTP using:

```env
SMTP_HOST=...
SMTP_PORT=...
SMTP_USER=...
SMTP_PASS=...
SMTP_FROM=...
```

Set `SMTP_SECURE=true` when your mail server requires implicit TLS.

Test SMTP delivery from Settings before relying on invitations.

Meeting invitations and their attached calendar event use the language selected in the creator's browser when the meeting is created:

- English
- Italian
- French
- German

`DEFAULT_UI_LANGUAGE` is used as the fallback for server-owned background emails such as dependency/update checks.

Meeting titles, notes, names and custom branding remain exactly as entered by the user.

## Interface languages

TiDiTalk includes:

- English
- Italian
- French
- German

Use the language selector in the main navigation bar. **Auto · Browser** detects the browser language and falls back to English.

The selected language is stored locally in the browser.

See `docs/LANGUAGES.md` for translation maintenance details.

## Recording

Recordings are created locally by the recording participant and downloaded to that participant's computer.

They are **not** stored as server-side backups.

Uploaded branding and background assets are stored in the `uploads` volume.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `M` | Microphone |
| `V` | Camera |
| `S` | Screen sharing |
| `D` | Drawing |
| `H` | Raise hand |
| `C` | Chat |
| `U` | Participants |

Drawing mode also supports pen, highlighter, arrow, rectangle, circle, laser pointer, undo and escape shortcuts.

## Updates

Back up first, then run:

```sh
git pull --ff-only
docker compose up -d --build
docker compose ps
```

If you update files manually via FTP, replace the changed project files and rebuild the app:

```sh
docker compose up -d --build app
```

Frontend files are included in the application image, so interface changes require a rebuild.

## Backups

Back up:

- `meeting_data` volume.
- `uploads` volume.
- Your private `.env`.

Because SQLite may use WAL mode, do not copy only a live `.db` file and ignore its WAL. Stop the application briefly for a filesystem-level backup or use SQLite's backup API.

A normal `docker compose down` preserves named volumes.

`docker compose down -v` removes them.

## Troubleshooting

**No user can sign in**  
Configure a valid bootstrap account in `USERS` and restart. Clean installations have no default users.

**Room opens but audio/video does not work**  
Verify `ANNOUNCED_IP`, NAT/firewall forwarding, mediasoup ports and TURN credentials.

**Problems on mobile or corporate networks**  
Test TURN reachability and determine whether the network requires TURN over TLS.

**Camera is blank**  
Check browser permissions, HTTPS and whether another application is already using the device.

**Face effects or backgrounds fail**  
Inspect the browser console and local `/assets/vendor/` resources.

**Interface still shows the old version**  
Rebuild the app image and hard-refresh the browser.

## Security notes

- Keep `.env` private.
- Use a unique `SERVER_SECRET` and TURN credentials.
- Serve the application over HTTPS.
- Restrict the application port when it is intended to be reached only through a reverse proxy.
- Configure `TRUST_PROXY` only for trusted reverse-proxy addresses.

See `SECURITY.md` and `docs/ANALYSIS.md` for additional notes.

## Project documentation

- `docs/LANGUAGES.md` — language maintenance.
- `docs/PUBLISHING.md` — GitHub publishing guide.
- `docs/ANALYSIS.md` — analysis and verification notes.
- `SECURITY.md` — security information.

## Support this project

TiDiTalk is developed and maintained in the open. If it saves you time, a donation helps keep it going.

[**Support TiDiTalk via PayPal**](https://paypal.me/raxiel87)

Contributions are welcome too: bug reports with clear reproduction steps, translations and documentation fixes are as valuable as code.

## Licence

TiDiTalk is free software released under the GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later). You may use, study, modify and redistribute it; if you distribute a modified version, or run one as a network service for other people, the corresponding source must be made available under the same licence. The full text is in `LICENSE`.

Dependency and bundled asset licences remain applicable.

Copyright © 2026 Giuseppe Sciarra / Tastiere Digitali.
