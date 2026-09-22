# Changelog

## 2.3.0 — 2026-09-22

- Published under the **GNU AGPL-3.0** licence.
- The connectors are neutral: no panel address and no registration key in the source.
  Each site can be configured from its own admin page, and Sentinel TD generates a
  ready-to-use WordPress package containing the address of *your* installation.
- **The connector packages ship with the application**: nothing to zip and nothing to upload.
  *Settings → Connectors* builds them from the bundled sources on request, and an uploaded zip
  is now only an optional override that can be removed with *Use the bundled one*.
- New setting *Settings → Connectors → Public address of this panel*, used to build the
  WordPress package; `scripts/build_connectors.py` accepts the same values for offline builds.

## 2.2.0 — 2026-09-22

- Redesigned statistics chart, detached from the period switch: round-number axis with
  guide lines, monthly-average line, stacked succeeded/failed bars, highlighted selected
  month, side tooltip (sites, components, change vs previous month), summary cards (total,
  monthly average, best month, change) and 6/12/24-month buttons.
- The download icon rendered as a stray character (the bundled Inter font has no ⬇ glyph):
  replaced with an SVG icon on every download button.

## 2.1.0 — 2026-09-22

- **Detailed reports on demand** (PDF or CSV) for one site, several sites, a folder or all
  sites, over any range of months: per component, how many times it was updated and from
  which version to which, plus the history of every single update with date and result.
- The monthly rollup now stores the starting version of each component, and the detailed
  update history is kept for a configurable number of days (default 400, in *Settings*).
- **Guided bulk install/remove**: platform, package or extension, target sites with folder
  shortcuts and filter. Removal lists only the sites that actually have the extension and
  never calls sites that don't; results distinguish succeeded, failed and skipped.
- Joomla connector **1.26.0**: `file`-type extensions (e.g. language packs) can be removed;
  Joomla core stays protected through its `protected`/`locked` flags.
- Connector sources included in `connectors/`, with `scripts/build_connectors.py` to build
  the installable packages without committing the registration key.
- Fixed every select with dynamic options showing the wrong initial value.

## 2.0.0

- New Sentinel TD interface: dashboard, folders, site detail with screenshot preview,
  statistics with month-over-month comparison, monthly PDF reports per folder or global.
- Editable email/Telegram notifications, domain and license expiry tracking with recurring
  renewals, security feed matching.
- Screenshot service on Google Chrome (H.264 background videos render), list thumbnails.
- Dependency security update (python-jose, jinja2, python-multipart, weasyprint, aiosmtplib),
  connector download protected by full authentication, rate limiting on the agent endpoints.
- Interface in English, Italian, French and German.
