# Operations

## Start

```bash
docker compose up --build -d
```

## Stop

```bash
docker compose down
```

## Rebuild

```bash
docker compose up --build -d
```

## Health Check

```bash
docker compose ps
```

Or query:

```text
GET http://localhost:9990/api/scheduler/status
```

## Persistent Data

The app stores mutable runtime state under the Docker volume mounted at `/vault`.

Expected contents include:

- `deepresearcher.sqlite3`
- `runtime-config.yaml`
- run digest outputs

## Backup Guidance

If you need backups, back up the vault volume or export SQLite plus runtime config. Do not use markdown blobs as the primary backup format for live operations.

## Production Notes

- The current server is the Flask development server.
- For production, run behind Gunicorn or another production-grade server.
- Put a reverse proxy in front if exposing the service externally.
