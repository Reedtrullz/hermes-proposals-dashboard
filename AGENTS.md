# AGENTS.md

## What this is
A lightweight FastAPI web dashboard that shows your projects and surfaces what needs attention — uncommitted changes, stale repos, and git state — by checking the actual directories in ~/Projectos/.

## Commands
- Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Dev server: `HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload`
- Compile check: `.venv/bin/python -m compileall -q main.py`
- Tests: `.venv/bin/python -m pytest -q`

## Architecture
- `main.py` — FastAPI app, SQLite migration layer, routes, and recommendation engine
- `templates/` — Jinja2 server-rendered pages (base.html shell, projects.html, project_detail.html, settings.html)
- SQLite lives at `$HERMES_HOME/proposals.db` (default `~/.hermes/proposals.db`)

## Routes
- `/proposals` → redirects to `/proposals/projects`
- `/proposals/projects` — main dashboard: project cards with git-aware recommendations
- `/proposals/projects/{id}` — project detail: proposals list, planning action
- `/proposals/settings` — basic configuration
- Old proposal detail routes still work for bookmarked links but are not in nav

## Recommendation engine
`project_recommendations()` checks in priority order:
1. Git state (uncommitted changes, unpushed commits, 14+ day stale repos)
2. Proposal state (pending decisions, waiting, in-progress, revision needed)
3. New-project guidance (track ongoing work for existing dirs, create first proposal for new ones)

Recommendations are local-only — no API calls, no AI. They use `git` subprocess calls against each project directory.

## Projects
- Projects table: `id, name, description, desired_outcome, status, created_at, updated_at`
- Created via the "New project" form on the dashboard
- Auto-populated from GitHub repos (see memory for bulk import)
- Statuses: active, paused, completed, archived

## Auth
- Deployed auth via Auth.js cookies and `AUTH_URL`
- Local dev: `HERMES_REQUIRE_AUTH=0`
- API bypass: `X-Hermes-Key: $HERMES_API_KEY`

## Deployment
- Local: launchd service (`com.reedtrullz.kanban-dashboard`) → `run.sh`
- Tunnel: autossh (`com.reedtrullz.kanban-tunnel`) → VPS port 8089
- VPS: Caddy reverse-proxies `/proposals*` to localhost:8089
- Live at https://reidar.tech/proposals/projects (`/proposals` redirects there)

## Style
- Keep it simple — small helper functions in main.py, no framework layers
- Idempotent SQLite migrations with `CREATE TABLE IF NOT EXISTS` and `ensure_column`
- Forms work without frontend build step
- Dark theme, terminal-inspired aesthetic
