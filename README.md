# Iterative Research Intelligence Workbench

[![CI](https://github.com/vaddhiparthy/Iterative-Research-Intelligence-Workbench/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/vaddhiparthy/Iterative-Research-Intelligence-Workbench/actions/workflows/ci.yml)

A containerized research workspace that keeps a set of topics under continuous
revision. Each research cycle asks a language model for authoritative sources,
fetches and extracts those pages, synthesizes a report, and folds in any steering
feedback the operator has left on the topic. State lives in SQLite inside a
mounted volume; the browser UI is a small Flask application.

A language-model endpoint is required. The default mode targets a local Ollama
server; an OpenAI-compatible chat-completions endpoint is the alternative.

## How It Works

A research cycle (`research_once`) runs per topic:

1. **Source discovery** — the topic title, description, and current feedback
   context are sent to the model with an instruction to return up to ten
   authoritative sources as `<Site Name> — <URL>` lines. Responses are parsed
   line by line, with a bare-URL fallback, and truncated to `max_sources`. If the
   model returns nothing usable, URLs already stored on the topic are reused.
2. **Fetch and extract** — each URL is requested with a `timeout_sec` budget, the
   response body is capped at `max_fetch_bytes`, then run through
   readability-lxml and BeautifulSoup to recover article text. Pages yielding
   under 400 characters are discarded. Each surviving page contributes up to
   12,000 characters of evidence.
3. **Synthesis** — the evidence block, topic, and feedback context go back to the
   model. The reply is split on a trailing `SOURCES:` marker into a report body
   and a source list; fetched sources are merged ahead of model-listed ones and
   deduplicated by URL. The report is stored capped at 50,000 characters and up
   to 20 sources are retained.
4. **Status transition** — topics in `queued` or `staging` become `active` on a
   successful cycle.

`iterate_once_sync` walks every non-archived topic under a non-blocking lock, so
a second trigger while a run is in flight returns immediately instead of queuing.
A run digest is written per cycle.

**Feedback.** Each topic carries a feedback thread. `feedback_context` builds the
prompt block as latest entry first (marked highest priority), then a summary of
older entries, then the recent thread. Once the thread exceeds 12 messages,
older entries are collapsed into a single truncated summary string rather than
being sent in full, keeping prompt size bounded.

**Scheduling.** APScheduler runs a single `iterate` interval job with
`max_instances=1` and coalescing. The control panel toggles it by pausing and
resuming the job rather than tearing the scheduler down, and interval changes
reschedule in place.

**Storage.** SQLite holds live topic state, revisions (capped at 50), sources,
and feedback. Markdown files are treated as an export and archival format, not
the operational datastore: incremental revisions, structured filtering, feedback
threads, and scheduler metadata are all poorly served by flat files. Legacy
markdown topics found in the vault are migrated into SQLite on startup.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Dashboard listing active and archived topics |
| `/submit` | Create a topic |
| `/topic/<rid>` | Topic view with report, sources, and feedback thread |
| `/topic/<rid>/run` | Run one cycle for a single topic |
| `/topic/<rid>/feedback` | Append steering feedback |
| `/topic/<rid>/stop` | Archive a topic |
| `/control` | Control panel for scheduler, provider, and fetch settings |
| `/control/save`, `/control/run-once`, `/control/scheduler-toggle` | Control-panel actions |
| `/vault`, `/index`, `/prompt` | Stored state and prompt inspection |
| `/api/scheduler/status`, `/api/scheduler/toggle`, `/api/scheduler/reschedule` | Scheduler API |
| `/api/run_once` | Trigger a cycle |

Operational controls are kept off the dashboard and live on `/control`.

## Configuration

`config.yaml` is baked into the image and supplies defaults. Control-panel
changes are merged over it and persisted to `/vault/runtime-config.yaml`, which
takes precedence on the next load.

| Key | Default | Purpose |
| --- | --- | --- |
| `vault_root` | `/vault` | Mounted state directory |
| `llm_mode` | `ollama` | `ollama` or `openai` |
| `ollama.base_url` | `http://host.docker.internal:11434` | Ollama endpoint reachable from the container |
| `ollama.model` | `llama3.1:8b` | Ollama model tag |
| `openai.api_key_env` | `OPENAI_API_KEY` | Environment variable holding the API key |
| `openai.model` | `gpt-4o-mini` | Model for the chat-completions call |
| `iteration_interval_minutes` | `60` | Scheduler interval, floored at 1 |
| `max_sources` | `10` | Sources fetched per cycle |
| `max_fetch_bytes` | `800000` | Per-page download cap, floored at 10,000 |
| `timeout_sec` | `15` | Per-page fetch timeout, floored at 5 |
| `scheduler_enabled` | `true` | Whether the interval job starts resumed |

Model calls use a fixed 180-second timeout. Timestamps are rendered in
`America/Detroit`.

## Run

```bash
docker compose up --build
```

The container publishes port 9990, so the local UI is at
`http://localhost:9990` on the host running Compose. The Compose healthcheck
polls the scheduler status endpoint from inside the container.

For the default `ollama` mode, an Ollama server must be reachable at the
configured `base_url`; from inside the container that is
`host.docker.internal:11434`. For `openai` mode, pass the API key through the
environment variable named by `openai.api_key_env`.

## Runtime State

The mounted `/vault` volume holds:

- `deepresearcher.sqlite3` — topics, revisions, sources, feedback
- `runtime-config.yaml` — control-panel overrides
- `index.md` and run digests — markdown exports

Vault contents are runtime data and are excluded from version control.

## Testing

There is no automated test suite. CI runs a dependency install and a syntax
check over `app/app.py` and `start.py` on every push and pull request
(`.github/workflows/ci.yml`). The same check locally:

```bash
python -m py_compile app/app.py start.py
```

Container state can be inspected with `docker compose ps` and the scheduler
status endpoint.

## Repository Layout

```text
.
├── app/
│   ├── app.py                 # Flask app, scheduler, SQLite state, research cycle
│   ├── requirements.txt
│   ├── static/                # JS, manifest, icons, service worker
│   └── templates/             # Dashboard, control panel, topic, submit views
├── config.yaml                # Default runtime configuration baked into the image
├── docker-compose.yml
├── Dockerfile
├── start.py                   # Container entrypoint
└── .github/workflows/ci.yml
```

## Scope And Limitations

- Single-container, single-process design; there is no worker pool and one lock
  serializes all research runs.
- No API keys are stored in the repository; OpenAI credentials are read from the
  environment.
- Source quality depends entirely on what the model returns. Fetches that fail,
  are paywalled, or yield too little text are silently skipped.
- No full-text search over stored reports, no structured run-log table, and no
  export pipeline beyond the markdown digests.
- The provider abstraction covers two modes only, with no retry or fallback
  between them.
- No automated tests beyond the CI syntax check.
- Generated research output is unreviewed model text and should be checked before
  it is relied on or shared.

## Rights

All rights are reserved. No use, copying, modification, distribution,
commercialization, private deployment, or derivative work is permitted without
the copyright holder's explicit prior written consent. See [LICENSE](LICENSE).
