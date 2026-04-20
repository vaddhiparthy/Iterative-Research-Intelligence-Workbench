# Architecture

## Overview

The application is a Flask-based research tracker with:

- SQLite-backed live state
- APScheduler-driven repeated research cycles
- Dockerized runtime
- HTML UI for dashboard, control panel, topic detail, and feedback

## Core Components

### `app/app.py`

Responsible for:

- loading base and runtime configuration
- initializing SQLite
- topic persistence
- scheduler lifecycle
- feedback summarization
- provider calls
- route handling

### SQLite State

The `topics` table stores:

- topic identity and metadata
- status
- report body
- sources
- revision history
- feedback summary
- recent feedback messages

This avoids repeated parsing of large markdown files and supports efficient incremental updates.

### Runtime Config

Two-layer config model:

- `config.yaml` for repo defaults
- `/vault/runtime-config.yaml` for operator overrides from the control panel

### Research Loop

Each cycle:

1. Selects non-archived topics.
2. Builds source-discovery prompt with user steering feedback.
3. Fetches source content.
4. Synthesizes a report.
5. Persists new report, sources, status, revision note, and timing metadata.

### Feedback Compression

Recent feedback is stored verbatim.
Older feedback is folded into a compact summary so later prompts stay bounded.
