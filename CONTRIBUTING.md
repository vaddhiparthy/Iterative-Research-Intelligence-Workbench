# Contributing

## Development Expectations

- Keep live state in SQLite, not markdown blobs.
- Do not commit runtime vault contents, generated databases, or local config overrides.
- Preserve the dashboard/control-panel separation.
- Keep feedback handling prompt-efficient by summarizing older steering messages.

## Local Workflow

1. Make changes.
2. Run a syntax check:
   ```bash
   python -m py_compile app/app.py start.py
   ```
3. Rebuild and verify:
   ```bash
   docker compose up --build
   ```
4. Confirm the following paths work:
   - `/`
   - `/control`
   - `/submit`
   - `/topic/<id>`

## Pull Requests

- Describe the user-visible impact.
- Note any schema changes.
- Mention whether runtime config behavior changed.
- Include manual verification steps.
