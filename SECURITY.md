# Security Policy

## Supported Scope

This repository is intended for a self-hosted research workflow. Security-sensitive areas include:

- provider credentials
- runtime vault contents
- research data retained in SQLite
- network calls to external providers and fetched sources

## Reporting

If you find a security issue, do not open a public issue with exploit details. Report it privately to the repository owner.

## Operational Guidance

- Keep API keys in environment variables, not source files.
- Do not commit `/vault` contents or generated databases.
- Review any research data before sharing publicly.
- If deploying beyond local development, replace the Flask dev server with a production WSGI/ASGI stack behind a reverse proxy.
