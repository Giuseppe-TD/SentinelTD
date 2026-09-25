# Changelog

## 2.4.0

### Added
- Daily view in statistics: day-by-day chart over 7, 30 or 90 days, with the rankings of
  sites and components for that period and a *by day of the week* summary
- Round-number axis, guide lines, average line, stacked succeeded/failed bars and a side
  tooltip (sites, components, change against the previous point) in both charts

### Changed
- The dashboard chart now uses the same design as the statistics one

### Fixed
- Language selector appearing over the toolbar buttons, or not appearing at all: it was
  created before the application rendered its topbar. Placement is now re-evaluated on
  every render
- Value labels overlapping the heading of the dashboard chart

## 2.3.0

### Added
- Connector packages built by the panel itself from the bundled sources: nothing to zip,
  nothing to upload
- Setting *Settings → Connectors → Public address of this panel*, written into the
  generated WordPress package together with the registration key
- `.github/FUNDING.yml` and a donation section

### Changed
- Published under the **GNU AGPL-3.0**
- The connector sources are neutral: no panel address and no key anywhere in the repository
- An uploaded connector zip is now only an optional override, removable with
  *Use the bundled one*
- `scripts/build_connectors.py` also accepts the panel address, for builds outside the panel

## 2.2.0

### Changed
- Redesigned statistics chart, detached from the period switch, with summary cards
  (total, monthly average, best month, change)

### Fixed
- The download icon rendered as a stray character: the bundled Inter font has no ⬇ glyph,
  replaced with an SVG icon on every download button

## 2.1.0

### Added
- Detailed reports on demand (PDF or CSV) for one site, several sites, a folder or all of
  them, over any range of months: per component, how many times it was updated and from
  which version to which, plus the history of every single update
- Guided bulk install and remove: platform, package or extension, target sites with folder
  shortcuts and a filter
- Starting version stored in the monthly rollup, and a configurable retention for the
  detailed update history (default 400 days)
- Connector sources in `connectors/`, with `scripts/build_connectors.py`

### Changed
- Removal lists only the sites that actually have the extension, and never calls the others;
  results distinguish succeeded, failed and skipped
- Joomla connector 1.26.0: `file`-type extensions such as language packs can be removed,
  while Joomla core stays protected through its `protected`/`locked` flags

### Fixed
- Selects with dynamic options showing the wrong initial value

## 2.0.0

### Added
- New Sentinel TD interface: dashboard, folders, site detail with preview, statistics with
  month-over-month comparison, monthly PDF reports (global or per folder)
- Editable email and Telegram notifications, domain and licence expiry tracking with
  recurring renewals, vulnerability matching
- Screenshot service on Google Chrome, so H.264 background videos render, with thumbnails
- Interface in English, Italian, French and German

### Changed
- Dependencies updated after a security audit (python-jose, Jinja2, python-multipart,
  WeasyPrint, aiosmtplib and others)
- Connector download now requires full authentication instead of a short-lived image token
- Rate limiting added to the endpoints used by the connectors

### Fixed
- Screenshot failures logged at info level, invisible in container logs for days
- Settings that reported themselves as saved without being sent
