# Contributing

A small server-rendered FastAPI dashboard. Contributions should keep it simple: projects, git-aware recommendations, local-only state checks.

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Use a temporary `HERMES_HOME` for isolated testing:

```bash
HERMES_HOME="$(mktemp -d)" HERMES_REQUIRE_AUTH=0 \
  .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089
```

## Rules

- Keep the Projects dashboard simple — no reintroducing proposal pipelines, agent teams, or workflow editors
- Keep migrations idempotent using existing SQLite patterns (`CREATE TABLE IF NOT EXISTS`, `ensure_column`)
- No paid API calls or LLM provider integrations
- Keep credentials, `.env`, SQLite files, and virtual environments out of commits
- Document any new recommendation types in AGENTS.md

## Validation

```bash
.venv/bin/python -m compileall -q main.py
.venv/bin/python -m pytest -q
```
