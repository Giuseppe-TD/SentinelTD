# Connectors

Sentinel TD talks to each website through a small connector installed on the site.

| Platform | Source | Current version |
|---|---|---|
| WordPress | `wordpress/td-panopticon/` | 2.15.0 |
| Joomla 4/5/6 | `joomla/plg_system_tdpanopticon/` | 1.26.0 |

The internal identifiers (`td-panopticon` folder, `tdpanopticon` Joomla element, REST
namespace `tdpanopticon/v1`, token option name) are kept from the original project name
on purpose: changing them would turn every installed connector into a *different*
plugin and disconnect the existing sites. Only the visible names say "Sentinel TD".

The sources here are **neutral**: no panel address, no registration key. Nothing in this
repository points at a particular installation.

## Getting a package (normal way)

The packages ship inside Sentinel TD: **nothing to zip, nothing to upload**.

1. Open *Settings → Connectors* and set **Public address of this panel**.
2. Press **Download** next to WordPress or Joomla. The panel builds the package from these
   sources; the WordPress one is written with your address and registration key, so a site
   connects itself right after activation.

Uploading a zip is only needed to override the bundled version (for example to test a newer
connector); *Use the bundled one* removes the override.

## Configuring a site by hand

Works with a neutral package too: install it, then in the site's own admin page
(*Settings → Sentinel TD* on WordPress) fill in the panel address and the registration key,
or simply copy the site token into Sentinel TD when adding the site.

## Building outside the panel

```sh
SENTINEL_HUB_URL=https://sentinel.example.com SENTINEL_HUB_KEY=your-key \
    python scripts/build_connectors.py

python scripts/build_connectors.py      # neutral package
```

Output in `dist/` (ignored by Git):

- `sentinel-td-wp-<version>.zip` → WordPress: *Plugins → Add New → Upload Plugin*
- `sentinel-td-jm-<version>.zip` → Joomla: *System → Install → Extensions*

Address and key are written only into the generated zip, never into the sources.
**Do not commit `dist/`**: a personalized WordPress package contains your key.

## Updating the connectors on all sites

Upload the new packages to *Settings → Connectors* (the archive keeps them as masters),
then use *Install* from the sites list on all WordPress or Joomla sites: the same folder
and element names mean the new version installs over the old one, keeping each site's
token and connection.
