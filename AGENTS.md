# AGENTS.md

## Commands
- Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Dev server: `HERMES_REQUIRE_AUTH=0 .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8089 --reload`
- Existing launcher: `./run.sh`
- Compile check: `.venv/bin/python -m compileall -q main.py`
- Tests: `.venv/bin/python -m pytest -q`
- Docker build: `docker build -t hermes-kanban-dashboard .`
- Compose self-host: copy `.env.example` to `.env`, edit secrets, then `docker compose up -d --build`
- Ansible syntax: `ansible-playbook ansible-playbook.yml --syntax-check`
- VPS deploy: `ansible-playbook ansible-playbook.yml`

## Architecture
- `main.py` is the FastAPI app, SQLite migration layer, route layer, seed data, and small data-access helpers.
- `templates/` contains server-rendered Jinja pages. Keep kanban cards centered around existing `/proposals` routes.
- SQLite lives at `$HERMES_HOME/proposals.db`; by default this is `~/.hermes/proposals.db`.
- New "agent operations" records are local SQLite tables only. Agent runs, costs, handoffs, approvals, and audit events are records; this app does not call paid LLM providers.
- `~/.hermes/proposals_trigger` is an existing integration point. New card creation writes the card id; approving a card writes `APPROVED:<id>`.

## Style
- Prefer small helper functions in `main.py` over new framework layers until the file becomes difficult to maintain.
- Use idempotent SQLite migrations with `CREATE TABLE IF NOT EXISTS` and `ensure_column`.
- Keep API compatibility for existing proposal/card routes.
- Forms should work without a bundled frontend build step. htmx is used only for lightweight polling on card detail pages.
- Use JSON text columns for simple lists/payloads that do not need relational querying yet.

## Auth And Local Dev
- Deployed auth is enabled by default through Auth.js cookies and `AUTH_URL`.
- Local development and tests should set `HERMES_REQUIRE_AUTH=0`.
- API clients may bypass cookie auth with `X-Hermes-Key: $HERMES_API_KEY`.

## Files And State To Avoid
- Do not commit or hand-edit `.venv/`, `__pycache__/`, `.DS_Store`, `*.pyc`, or `*.log`.
- Do not mutate a user's real `~/.hermes/proposals.db` in tests; use a temporary `HERMES_HOME`.
- Do not delete or repurpose `~/.hermes/proposals_trigger`; it is part of the existing agent loop.
- Treat `deploy/inventory/hosts.yml` as operational configuration and avoid changing it unless the deployment target changes.
- Do not hard-code provider API keys or add paid external services.
- It is acceptable to keep the encrypted `group_vars/vps/vault.yml` with the project. Never commit `.ansible-vault-pass`; the local password file is intentionally ignored for agentic deploys.
