# Project Dashboard

A lightweight dashboard that shows your projects and what needs attention — by checking actual git state in `~/Projectos/`.

**[Open Dashboard](https://reidar.tech/proposals/projects)**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-server--rendered-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local--first-003B57?logo=sqlite&logoColor=white)

## What it does

Each project card shows recommendations based on real state:
- **Uncommitted changes** — finish up and push before context-switching
- **Unpushed commits** — branch has work that hasn't hit remote
- **Stale repos** — no commits in 14+ days, is it stalled?
- **In-flight projects** — code exists at ~/Projectos/<name>, track ongoing work
- **New projects** — no local directory yet, create a first proposal

The dashboard is local-first and does not call LLM APIs in the web request path. It can coordinate external agent/executor workflows through explicit records, trigger files, and approval states.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Open http://127.0.0.1:8089/proposals/projects

## Deployment

Runs behind Caddy on a VPS, tunneled via autossh from the local machine. See `AGENTS.md` for full deployment docs.

## Tech

FastAPI + Jinja2 + SQLite + htmx. Server-rendered, no frontend build step. Dark theme.
