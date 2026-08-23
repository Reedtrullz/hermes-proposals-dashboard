# About Project Dashboard

A lightweight dashboard that surfaces what your projects need next — by checking actual git state in `~/Projectos/`.

The dashboard is local-first and does not call LLM APIs in the web request path. It can coordinate external agent/executor workflows through explicit records, trigger files, and approval states.

## What it does

Each project card shows one recommendation based on real state:
- **Uncommitted changes** — finish and push before context-switching
- **Unpushed commits** — branch has work not on remote
- **Stale repos** — no commits in 14+ days
- **In-flight projects** — code exists, track ongoing work
- **New projects** — no local directory yet

Recommendations are local-only: `git status`, `git log`, and directory checks. Agent and executor coordination is represented as explicit dashboard state, not hidden web-request inference.

## Design Principles

### Honest state
The dashboard reads your actual directories. It doesn't guess, and it records worker-related activity only when explicit proposal, workflow, trigger, or approval state exists.

### Explainable
Every recommendation has a clear reason — "Heimdall has uncommitted changes" is something you can verify with `cd ~/Projectos/Heimdall && git status`.

### Local-first
SQLite, git subprocess, and filesystem checks. Runs on your machine, serves over an SSH tunnel to your VPS.

## What It Covers
- Project overview with git-aware recommendations
- Project detail pages
- Basic project CRUD (create, edit, archive)
- Settings

## Integration Boundary
- The web request path stays local-first and does not invoke LLM providers.
- External agents or executors can be coordinated through stored records, trigger files, and approval states.
- Budgets, workflows, handoffs, and audit events are tracked as operational metadata for explicit dashboard actions.

## Deployment
Hosted at [reidar.tech/proposals/projects](https://reidar.tech/proposals/projects). Can run locally: `HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --port 8089`.
