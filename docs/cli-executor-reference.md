# CLI Executor Reference

This dashboard supports routing work to 7 different execution backends — from native
Hermes agents to external CLI coding agents. This document covers installation,
verification, and governance for each.

## Quick Reference

| Executor Type | Binary | Package | Headless Command | Auto-Approve Flag |
|---|---|---|---|---|
| `hermes` | (native) | — | Hermes agent loop | — |
| `codex` | `codex` | `@openai/codex` | `codex exec "prompt"` | `--full-auto` |
| `claude-code` | `claude` | `@anthropic-ai/claude-code` | `claude -p "prompt"` | `--dangerously-skip-permissions` |
| `opencode` | `opencode` | `opencode-ai` | `opencode run "prompt"` | `--dangerously-skip-permissions` |
| `agy` | `agy` | Antigravity IDE | `agy exec "prompt"` | (via IDE) |
| `command-code` | `cmd` | `command-code` | `cmd -p "prompt"` | `--yolo` |
| `kilo` | `kilo` | `@kilocode/cli` | `kilo run --auto "prompt"` | `--auto` |

## Installation

### Codex (OpenAI)
```bash
npm install -g @openai/codex
codex --version         # verify
codex login             # auth
```

### Claude Code (Anthropic)
```bash
npm install -g @anthropic-ai/claude-code
claude --version        # verify
claude                  # first run to auth
```

### OpenCode
```bash
npm install -g opencode-ai
# or: brew install anomalyco/tap/opencode
opencode --version      # verify
opencode auth login     # configure providers
```

### Command Code
```bash
npm install -g command-code
cmd --version           # verify (note: binary is 'cmd', not 'command-code')
cmd login               # auth
```
⚠️ Requires Node.js 20+. v18 crashes with regex error.

### Kilo Code
```bash
npm install -g @kilocode/cli
kilo --version          # verify (note: binary is 'kilo', not 'kilocode')
kilo                    # first run, then /connect to add API keys
```

### Antigravity (Google)
```bash
# Install via Antigravity IDE: Cmd+Shift+P → "Shell Command: install 'agy' command"
agy --version           # verify
```

## Your Machine (Reidar — verified 2026-05-22)

| CLI | Installed | Version | Binary Path |
|---|---|---|---|
| Codex | ✅ | 0.125.0 | fnm bin |
| Claude Code | ✅ | 2.0.76 | `~/.local/bin/claude` |
| OpenCode | ✅ | 1.14.46 | `/opt/homebrew/bin/opencode` |
| Command Code (cmd) | ✅ | latest | fnm bin (needs Node 20+) |
| Kilo Code (kilo) | ✅ | 7.3.1 | fnm bin |
| Antigravity (agy) | ❌ | — | Not installed |

## Architecture: How Routing Works

```
Dashboard (reidar.tech/proposals)
  │
  ├─ User creates card, assigns to agent with executor_type="codex"
  │
  ├─ Writes ~/.hermes/proposals_trigger         → "p_abc123"
  ├─ Writes ~/.hermes/proposals_trigger_executor → {"proposal_id":"p_abc123","executor_type":"codex",...}
  │
  ▼
External Hermes agent loop
  │
  ├─ Reads proposals_trigger → gets card ID
  ├─ Reads proposals_trigger_executor → gets executor type
  ├─ Looks up agent from DB via GET /api/proposals/{id}/executor
  │
  ├─ If executor=hermes: spawn Hermes worker profile
  └─ If executor=codex: spawn codex exec --full-auto "task from card body"
```

### API Endpoints

`GET /api/proposals/{id}/executor`
```json
{
  "proposal_id": "p_abc123",
  "executor": {
    "agent_id": "agent_codex_coder",
    "agent_name": "Codex Coder",
    "executor_type": "codex",
    "executor_label": "Codex CLI"
  }
}
```

Returns `"executor": null` for native Hermes agents.

### Trigger File Format

`~/.hermes/proposals_trigger` — unchanged. Contains card ID or `APPROVED:<id>`.

`~/.hermes/proposals_trigger_executor` — JSON metadata (only written for non-hermes executors):
```json
{"proposal_id":"p_abc123","agent_id":"agent_yyy","executor_type":"codex","executor_label":"Codex CLI"}
```

## Safety Levels (recommended escalation)

1. **`--full-auto`** (Codex) / **`-p`** (Claude) / **`--auto`** (Kilo) — Sandboxed, workspace-scoped. Safe default.
2. **`--ask-for-approval never`** (Codex) / **`--dangerously-skip-permissions`** (Claude) — Unattended, still sandboxed.
3. **`--dangerously-bypass-approvals-and-sandbox`** (Codex) / **`--yolo`** (cmd) — NO sandbox. Only for ephemeral CI containers.

Start at level 1. Only escalate if the task genuinely requires it AND the environment is disposable.

## Governance Suggestions

1. **Per-agent cost budgets** — Each CLI delegator template has a `monthly_budget_usd`. The dashboard tracks actual vs budget. Set alerts before giving `--yolo` access.

2. **Worktree isolation** — Always spawn external CLIs in `git worktree` branches. Never in the main checkout. The `kanban-codex-lane` skill documents this pattern for Codex; same principles apply to all CLIs.

3. **Hermes owns the lifecycle** — External CLIs are implementation lanes. Hermes always: reviews the diff, runs the tests, and calls `kanban_complete`. Never trust CLI self-report.

4. **Approval gates** — Cards routed to `--yolo` executors should have `risk_level: high` or `critical` to trigger the approval policy pipeline before execution.

5. **Provider diversity** — Having multiple CLI options means you're not locked into one provider's reliability or pricing. The orchestrator can route based on availability and cost.

6. **Auth rotation** — Each CLI manages its own auth. Rotate API keys independently. A compromised Codex key shouldn't affect Claude Code tasks.

7. **Node.js version matrix** — CLI tools have different Node requirements:
   - Codex: Node 18+
   - Claude Code: Node 18+
   - Command Code (cmd): Node 20+ ⚠️ (v18 crashes)
   - Kilo: Node 18+
   - Use `fnm use <version>` to switch if needed

## Suggested Workflow for Setup

1. Install desired CLIs (see Installation section above)
2. Create agents from the Setup page using the CLI templates
3. Create a test card assigned to a CLI-delegator agent
4. Verify the trigger executor file is written correctly
5. Configure the external agent loop to check `proposals_trigger_executor`
6. Start with `--full-auto` / `-p` / `--auto` safety level
7. Review the first few runs manually before scaling up
