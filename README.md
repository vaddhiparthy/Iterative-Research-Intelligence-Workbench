# Astra-X-Deepresearcher

A containerized deep-research workspace for iterative topic tracking, scheduled research cycles, per-topic user feedback, and a browser-based control panel.

## What It Does

- Tracks active and archived research topics.
- Runs repeated research cycles on a scheduler or on demand.
- Stores live topic state in SQLite for efficient retrieval and updates.
- Supports per-topic feedback threads so new user steering is incorporated into later runs.
- Exposes a standalone control panel for scheduler, provider, and fetch/runtime settings.
- Runs in Docker with a persistent mounted vault volume.

## Why SQLite Instead Of Markdown

Markdown is useful for exports and human-readable archives, but it is a poor primary datastore for:

- many incremental revisions
- structured filtering and retrieval
- feedback threads
- large topic bodies
- operational metadata such as scheduler status and run history

This project now uses SQLite for live application state. Markdown should be treated as an export or archival format, not the operational database.

## Project Structure

```text
.
├── app/
│   ├── app.py                 # Flask app, scheduler, SQLite state, feedback flow
│   ├── requirements.txt       # Python runtime dependencies
│   ├── static/                # JS, manifest, icons, service worker
│   └── templates/             # Dashboard, control panel, topic, submit views
├── config.yaml                # Default runtime configuration baked into the image
├── docker-compose.yml         # Local container orchestration
├── Dockerfile                 # Primary image build
├── start.py                   # Container entrypoint
├── .dockerignore
├── .gitignore
├── CONTRIBUTING.md
├── LICENSE
├── README.md
├── SECURITY.md
└── docs/
    ├── ARCHITECTURE.md
    └── OPERATIONS.md
```

## Features

### Dashboard

- Lists only active and archived topics.
- Keeps operational controls out of the main dashboard.

### Control Panel

- Separate `/control` page.
- Toggle scheduler.
- Trigger run-now.
- Change iteration interval.
- Change provider mode and model selection.
- Tune request timeout, source count, and fetch-size limits.

### Topic Feedback

- Each topic has a lightweight feedback thread.
- Feedback is timestamped and persisted.
- Older feedback is summarized to keep prompts efficient.
- Newer feedback is emphasized during later research runs.

## Runtime Storage

The container writes live state into the mounted `/vault` volume:

- `deepresearcher.sqlite3`
- `runtime-config.yaml`
- optional run digests

Do not commit runtime vault contents into source control.

## Quick Start

### Docker Compose

```bash
docker compose up --build
```

Then open:

```text
http://localhost:9990
```

## Configuration

`config.yaml` contains default settings such as:

- `llm_mode`
- `ollama.base_url`
- `ollama.model`
- `openai.api_key_env`
- `openai.model`
- `iteration_interval_minutes`
- `max_sources`
- `max_fetch_bytes`
- `timeout_sec`

At runtime, operator changes made in the control panel are persisted to `/vault/runtime-config.yaml`.

## Security And Privacy

- No API keys are stored in the repository.
- OpenAI credentials are expected through environment variables.
- Runtime database and vault contents are intentionally excluded from Git.
- Any generated research data should be reviewed before public sharing.

## Development

### Local Syntax Check

```bash
python -m py_compile app/app.py start.py
```

### Container Health

```bash
docker compose ps
```

### Scheduler Status

```text
GET /api/scheduler/status
```

## Roadmap

- structured run logs table
- full-text search over topic reports
- markdown or PDF export pipeline
- stronger provider abstraction
- automated tests and CI expansion

## Rights

All rights are reserved. No use, copying, modification, distribution,
commercialization, private deployment, or derivative work is permitted without
the copyright holder's explicit prior written consent. See [LICENSE](LICENSE).
