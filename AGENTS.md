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

## CLI Executor System (Multi-Provider Orchestration)

The dashboard supports routing work to 7 different execution backends via the `executor_type` field on agents.

### Agent executor types
| Type | CLI Binary | Package | Headless Command |
|------|-----------|---------|------------------|
| `hermes` | (native) | — | Hermes agent loop |
| `codex` | `codex` | `@openai/codex` | `codex exec --full-auto "prompt"` |
| `claude-code` | `claude` | `@anthropic-ai/claude-code` | `claude -p "prompt"` |
| `opencode` | `opencode` | `opencode-ai` | `opencode run "prompt"` |
| `agy` | `agy` | Antigravity IDE | `agy exec "prompt"` |
| `command-code` | `cmd` | `command-code` | `cmd -p "prompt"` |
| `kilo` | `kilo` | `@kilocode/cli` | `kilo run --auto "prompt"` |

Key gotcha: `command-code` npm package produces binary `cmd`, NOT `command-code`. `kilo` npm package is `@kilocode/cli`, binary is `kilo`.

### Trigger files
- `~/.hermes/proposals_trigger` — UNCHANGED. Contains card ID or `APPROVED:<id>`. Never repurpose.
- `~/.hermes/proposals_trigger_executor` — JSON metadata written when a card's assigned agent has a non-hermes executor. Format: `{"proposal_id":"p_abc","agent_id":"agent_yyy","executor_type":"codex","executor_label":"Codex CLI"}`. Only exists for non-hermes executors.

### Agent template keys
12 templates: `product_lead`, `architect`, `builder`, `reviewer`, `qa`, `cost_controller` (hermes native), plus `codex_coder`, `claude_coder`, `opencode_coder`, `agy_coder`, `commandcode_coder`, `kilo_coder` (CLI delegators).

### Key API endpoints
- `GET /api/proposals/{id}/executor` — returns executor routing info or `null` for hermes agents
- `GET /api/agents/{id}/executor-status` — JSON: checks if CLI binary is on PATH, runnable, returns version
- `GET /api/agents/{id}/executor-status-ui` — HTML fragment: styled badge for htmx live verification
- `POST /api/proposals/dry-run` — creates a `[DRY-RUN]` test proposal for a CLI agent, writes trigger files

### Safety
- `codex` and `command-code` executors auto-trigger a "Dangerous executor requires approval" policy (they have `--yolo`/`--dangerously-bypass` flags that can escape sandbox)
- `command-code` requires Node.js 20+ — the agent detail page shows a warning if Node < 20 is detected

### External agent loop integration
The external Hermes agent loop should:
1. Read `~/.hermes/proposals_trigger` for the card ID (as before)
2. Read `~/.hermes/proposals_trigger_executor` if it exists
3. If executor is non-hermes: spawn the appropriate CLI instead of a Hermes worker
4. The CLI output is reconciled by the worker profile (see `kanban-codex-lane` skill for the pattern)
5. After the CLI finishes, Hermes reviews the diff, runs tests, and calls `kanban_complete`

See `docs/cli-executor-reference.md` for complete CLI installation and governance documentation.

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
