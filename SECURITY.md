# Security and runtime data

This source release includes no production database, database journal, local environment file, uploaded user content or saved service credentials. Blank secret fields in `.env.example` must be configured before deployment. The application initializes its own schema in a new database.

Keep `.env`, database volumes and upload volumes private and back them up together. Use HTTPS, maintain dependency updates, restrict administrative access and expose only the documented ports. Built-in defaults are configuration examples, not production credentials. Do not publish a real `.env`, database export or support log in an issue.

Report a suspected vulnerability privately to the repository maintainer using GitHub private vulnerability reporting when enabled. Include affected versions, reproduction steps and impact, with sensitive values removed. No security contact address has been invented for this release.

The release checks verify syntax, translation behavior and exclusion of archived secrets/data. They are not a full penetration test or a substitute for deployment validation.
