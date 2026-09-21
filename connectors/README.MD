# Sentinel TD CMS connectors

This directory is reserved for the official connector packages distributed with Sentinel TD.

Add the two release archives here:

- the WordPress connector ZIP;
- the Joomla connector ZIP.

The exact ZIP filenames may follow the connector projects' own versioning scheme. The main Sentinel TD README intentionally links to this directory instead of hard-coding filenames, so connector releases can be replaced without rewriting the documentation.

## Important

These ZIP files are installed on the target WordPress/Joomla websites. They are **not** unpacked into the Sentinel TD source tree and they are **not** Docker Compose services.

The GitHub `connectors/` directory is also separate from the connector archive stored by a running Sentinel TD instance in its Docker `connectors` volume. To distribute a connector through the Sentinel interface, upload the package there as well.

Before committing a connector archive, verify that it contains no credentials, registration keys, site tokens, private `.env` files, backups or customer-specific configuration.
