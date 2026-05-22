# CLI Executor Watertight Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Close every gap in the CLI executor routing system — health checks, safety policies, cost tracking, guardrails, tests, and docs.

**Architecture:** Six independent feature strands, each touching a focused set of files. All changes are additive to the existing FastAPI + SQLite + Jinja2 pattern. No breaking changes to existing routes or trigger file format.

**Tech Stack:** Python 3.10+, FastAPI, SQLite (WAL mode), Jinja2, pytest + TestClient

**Current state:** 7 executor types supported, 6 CLI skills documented, executor column migrated, trigger executor file working, API endpoint returning executor info. 12 tests pass.

---

### Task 1: Add executor_type tracking to usage_records

**Objective:** Tag cost records with which CLI executor was used, so per-executor spend is visible.

**Files:**
- Modify: `main.py:583-583` (schema + ensure_column)
- Modify: `main.py:1300-1303` (agent detail — show executor breakdown)
- No new templates needed — inline enrichment

**Step 1: Add column to schema**

```python
# In create_schema(), after the usage_records CREATE TABLE:
ensure_column(db, "usage_records", "executor_type", "TEXT NOT NULL DEFAULT ''")
```

**Step 2: Update the record-cost form to include executor_type**

In `agent_detail.html`, after the "Model" field in the Record Cost form, add:

```html
<div class="field"><label>Executor Type</label>
  <input name="executor_type" value="{{ agent.get('executor_type', '') }}" readonly style="opacity:0.6">
</div>
```

Pre-fills from the agent's executor_type. Readonly because the cost is always for that agent's executor.

**Step 3: Update the record-cost route to accept executor_type**

Find the `/api/proposals/usage` POST route and add `executor_type: str = Form("")` to its parameters, then include it in the INSERT.

**Step 4: Add per-executor cost summary to agent detail**

In `agent_detail.html`, below the "All-time" spend line:

```html
{% if agent.get('executor_type', 'hermes') != 'hermes' %}
<div class="small muted" style="margin-top:4px">
  Executor spend (all-time): {{ money(agent.executor_spend_usd or 0) }}
</div>
{% endif %}
```

Compute `executor_spend_usd` in the `agent_detail` route:
```python
agent["executor_spend_usd"] = float(db.execute(
    "SELECT COALESCE(SUM(actual_cost_usd),0) FROM usage_records WHERE scope_type='agent' AND scope_id=? AND executor_type!=''",
    (agent_id,)
).fetchone()[0] or 0)
```

**Step 5: Commit**

```bash
git add main.py templates/agent_detail.html
git commit -m "feat: add executor_type tracking to usage_records"
```

---

### Task 2: Add executor health check endpoint

**Objective:** `GET /api/agents/{agent_id}/executor-status` verifies the CLI binary exists, is runnable, and has valid auth. Returns structured status so the dashboard and external consumers know if a CLI is ready.

**Files:**
- Modify: `main.py` (new route)

**Route design:**

```python
@app.get("/api/agents/{agent_id}/executor-status")
async def api_agent_executor_status(agent_id: str):
    with db_connect() as db:
        agent = row(db.execute("SELECT id, name, executor_type FROM agents WHERE id=?", (agent_id,)))
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    executor_type = agent.get("executor_type", "hermes")
    if executor_type == "hermes":
        return {"agent_id": agent_id, "executor_type": "hermes", "status": "native", "ready": True}

    # Map executor_type to binary name and version command
    BINARY_MAP = {
        "codex": ("codex", "codex --version"),
        "claude-code": ("claude", "claude --version"),
        "opencode": ("opencode", "opencode --version"),
        "agy": ("agy", "agy --version"),
        "command-code": ("cmd", "cmd --version"),
        "kilo": ("kilo", "kilo --version"),
    }

    binary, version_cmd = BINARY_MAP.get(executor_type, (None, None))
    if not binary:
        return {"agent_id": agent_id, "executor_type": executor_type, "status": "unknown_executor", "ready": False}

    import subprocess, shutil

    # Check 1: binary on PATH
    which = shutil.which(binary)
    if not which:
        return {"agent_id": agent_id, "executor_type": executor_type, "binary": binary, "status": "not_found", "ready": False, "path": None}

    # Check 2: binary is runnable
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        version_output = result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return {"agent_id": agent_id, "executor_type": executor_type, "binary": binary, "status": "timeout", "ready": False, "path": which}
    except FileNotFoundError:
        return {"agent_id": agent_id, "executor_type": executor_type, "binary": binary, "status": "not_found", "ready": False, "path": which}
    except Exception as exc:
        return {"agent_id": agent_id, "executor_type": executor_type, "binary": binary, "status": "error", "ready": False, "path": which, "error": str(exc)}

    return {
        "agent_id": agent_id,
        "executor_type": executor_type,
        "binary": binary,
        "status": "ok",
        "ready": True,
        "path": which,
        "version": version_output[:200],  # truncate long version strings
    }
```

**Step 1: Add the import at top**

```python
import shutil
import subprocess
```

Already have `import os`, `import time` etc.

**Step 2: Add the route** (anywhere in the route section, after the executor API endpoint)

**Step 3: Run verification**

```bash
curl -s http://127.0.0.1:8089/api/agents/agent_builder/executor-status | python3 -m json.tool
# Expected: {"agent_id":"agent_builder","executor_type":"hermes","status":"native","ready":true}

# For a codex agent:
curl -s http://127.0.0.1:8089/api/agents/<codex_agent_id>/executor-status | python3 -m json.tool
# Expected: {"executor_type":"codex","binary":"codex","status":"ok","ready":true,"path":"/Users/reidar/.../bin/codex","version":"codex-cli 0.125.0"}
```

**Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add executor health check endpoint GET /api/agents/{id}/executor-status"
```

---

### Task 3: Add "Verify CLI" button to agent detail page

**Objective:** A button on the agent detail page that calls the health check endpoint and shows the result inline with htmx.

**Files:**
- Modify: `templates/agent_detail.html`

**Step 1: Add the button and result area**

In agent_detail.html, in the top card after the executor display line:

```html
{% set exec = agent.get('executor_type', 'hermes') %}
{% if exec != 'hermes' %}
<div style="margin-top:10px">
  <button class="btn btn-sm" hx-get="/api/agents/{{ agent.id }}/executor-status" hx-target="#executor-status-result" hx-swap="innerHTML">
    Verify CLI
  </button>
  <span id="executor-status-result" class="small"></span>
</div>
{% endif %}
```

The htmx call hits the JSON endpoint. We need a small JS snippet or an HTML fragment endpoint to render it nicely. Since the endpoint returns JSON, we can either:

Option A: Use a client-side htmx extension to render JSON
Option B: Add an HTML fragment version

Simplest fix: add an HTML fragment endpoint or render the JSON in a small inline script.

**Step 2: Add HTML fragment endpoint (cleaner)**

```python
@app.get("/api/agents/{agent_id}/executor-status-ui", response_class=HTMLResponse)
async def api_agent_executor_status_ui(agent_id: str):
    result = await api_agent_executor_status(agent_id)
    if isinstance(result, JSONResponse):
        return HTMLResponse("<span class='badge' style='background:#f8514933;color:#f85149'>Not found</span>")
    ready = result.get("ready", False)
    status = result.get("status", "unknown")
    if ready:
        return HTMLResponse(f"<span class='badge' style='background:#3fb95033;color:#3fb950'>Ready — {result.get('version', result.get('path', ''))}</span>")
    else:
        return HTMLResponse(f"<span class='badge' style='background:#f8514933;color:#f85149'>Not ready — {status}</span>")
```

Then the button's `hx-get` targets this.

**Step 3: Commit**

```bash
git add main.py templates/agent_detail.html
git commit -m "feat: add Verify CLI button on agent detail with htmx health check"
```

---

### Task 4: Add YOLO executor safety approval policy

**Objective:** When a card is assigned to an agent with a `--yolo`/dangerous executor AND the card goes to `approved` status, auto-create an approval request. Uses existing `ensure_policy_approvals()` infrastructure.

**Files:**
- Modify: `main.py` (new policy + `ensure_policy_approvals` extension)
- Modify: `main.py:717-720` (seed policy)

**Step 1: Define which executors are "dangerous"**

```python
DANGEROUS_EXECUTORS = {"command-code", "codex"}  # --yolo / --dangerously-bypass capable
# Others use --auto / -p which are sandboxed
```

Actually, a better approach: any non-hermes executor that doesn't guarantee sandboxing. But for now, flag the ones with `--yolo` / `--dangerously-bypass` flags as dangerous. We could also make this configurable per-agent. Simpler: add a `requires_approval` boolean on agents that defaults to `True` for `command-code` and `codex` (their `--yolo` flags bypass sandbox).

Simplest approach: check in `ensure_policy_approvals()` whether the assigned agent uses a dangerous executor.

**Step 2: Extend `ensure_policy_approvals`**

```python
def ensure_policy_approvals(db: sqlite3.Connection, proposal_id: str) -> None:
    proposal = row(db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)))
    if not proposal:
        return
    total_cost = card_cost(db, proposal_id)

    # Existing policies
    if proposal.get("risk_level") == "critical":
        create_approval(db, "proposal", proposal_id, "Critical-risk card requires approval", "critical",
            "Default policy requires human approval for critical-risk cards.",
            payload={"risk_level": "critical"})
    if total_cost > 2.0:
        create_approval(db, "proposal", proposal_id, "Cost threshold exceeded", "high",
            "Default policy requires approval when estimated/manual card cost exceeds $2.00.",
            payload={"estimated_total_cost_usd": total_cost})

    # NEW: Dangerous executor safety policy
    agent_id = proposal.get("assigned_agent_id")
    if agent_id:
        agent = row(db.execute("SELECT executor_type, name FROM agents WHERE id=?", (agent_id,)))
        if agent and agent.get("executor_type") in ("command-code", "codex"):
            label = EXECUTOR_LABELS.get(agent["executor_type"], agent["executor_type"])
            create_approval(db, "proposal", proposal_id,
                f"Dangerous executor ({label}) requires approval",
                "high",
                f"Agent '{agent['name']}' uses {label} which can bypass sandboxing with --yolo/--dangerously-bypass flags. Human approval required before execution.",
                payload={"executor_type": agent["executor_type"], "agent_id": agent_id})
```

**Step 3: Seed the policy**

```python
# In seed_defaults(), add:
("policy_dangerous_executor", "Dangerous-executor cards require approval",
    {"entity_type": "proposal", "executor_dangerous": True}, "require_approval"),
```

**Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add YOLO/dangerous executor safety approval policy"
```

---

### Task 5: Add Node.js version guard for command-code agents

**Objective:** When creating or editing a `command-code` agent, check that Node.js >= 20 is available. Warn in the UI if not.

**Files:**
- Modify: `main.py` (helper + route enrichment)
- Modify: `templates/agent_detail.html` (warning display)
- Modify: `templates/agents.html` (warning in create form)

**Step 1: Add Node.js version check helper**

```python
def get_node_version() -> tuple[int, int] | None:
    """Return (major, minor) or None if node not found."""
    import subprocess, shutil
    node = shutil.which("node")
    if not node:
        return None
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        v = result.stdout.strip().lstrip("v")
        parts = v.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except Exception:
        return None
```

**Step 2: Add warning to agent detail page**

In `agent_detail.html`, after the executor display, for command-code agents:

```html
{% set exec = agent.get('executor_type', 'hermes') %}
{% if exec == 'command-code' %}
  {% set node_ver = agent.get('node_version') %}
  {% if node_ver and node_ver[0] < 20 %}
  <div class="card" style="margin-top:10px;border-left:3px solid #f85149">
    <p class="small" style="color:#f85149"><strong>⚠ Node.js {{ node_ver[0] }}.{{ node_ver[1] }} detected.</strong> Command Code requires Node.js 20+. CLI will crash with regex error on v18. Run <code>fnm use 20</code> or upgrade.</p>
  </div>
  {% endif %}
{% endif %}
```

**Step 3: Enrich agent detail route with node version**

In `agent_detail()` route:
```python
if agent.get("executor_type") == "command-code":
    agent["node_version"] = get_node_version()
```

**Step 4: Add warning to create form in agents.html**

In agents.html, below the executor selector, add:

```html
<p class="tiny muted" id="executor-note" style="margin-top:4px"></p>
<script>
document.querySelector('select[name="executor_type"]').addEventListener('change', function(e) {
  var note = document.getElementById('executor-note');
  if (e.target.value === 'command-code') {
    note.innerHTML = '⚠ Requires Node.js 20+. Verify with <code>node --version</code>.';
    note.style.color = '#f85149';
  } else {
    note.innerHTML = '';
  }
});
</script>
```

**Step 5: Commit**

```bash
git add main.py templates/agent_detail.html templates/agents.html
git commit -m "feat: add Node.js 20+ guard for command-code agents"
```

---

### Task 6: Add exhaustive tests

**Objective:** Test every new feature: executor column, templates, API endpoint, trigger file, health check, YOLO policy, Node guard.

**Files:**
- Modify: `tests/test_agent_ops.py`

**Step 1: Test executor_type column migration**

```python
def test_executor_type_column_exists_and_defaults_to_hermes(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with main.db_connect() as db:
        cols = {r["name"] for r in db.execute("PRAGMA table_info(agents)").fetchall()}
        assert "executor_type" in cols
        agent = db.execute("SELECT executor_type FROM agents WHERE id='agent_builder'").fetchone()
        assert agent["executor_type"] == "hermes"
```

**Step 2: Test CLI template creation**

```python
def test_create_codex_agent_from_template_has_correct_executor(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    resp = client.post("/api/proposals/agents/from-template",
        data={"template_key": "codex_coder"}, follow_redirects=False)
    assert resp.status_code == 303
    agent_id = resp.headers["location"].split("agent_id=", 1)[1]
    with main.db_connect() as db:
        agent = db.execute("SELECT executor_type, provider, model_name FROM agents WHERE id=?", (agent_id,)).fetchone()
        assert agent["executor_type"] == "codex"
        assert agent["provider"] == "openai"
```

**Step 3: Test executor API endpoint**

```python
def test_executor_api_endpoint_returns_null_for_hermes(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    client.post("/api/proposals", data={"title": "Test", "assigned_agent_id": "agent_builder"})
    # agent_builder has hermes executor
    resp = client.get("/api/proposals/p_0000000000/executor")  # will 404, need actual ID
    # Fix: get the actual proposal ID
    with main.db_connect() as db:
        pid = db.execute("SELECT id FROM proposals WHERE title='Test'").fetchone()["id"]
    resp = client.get(f"/api/proposals/{pid}/executor")
    assert resp.status_code == 200
    data = resp.json()
    assert data["executor"] is None  # hermes = None
```

**Step 4: Test trigger executor file**

```python
def test_trigger_executor_file_written_for_codex_agent(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    # Create a codex agent
    client.post("/api/agents", data={
        "name": "Test Codex", "role_title": "Coder",
        "provider": "openai", "model_name": "gpt-5",
        "executor_type": "codex", "tools_allowed": "comment",
        "monthly_budget_usd": "50",
    })
    with main.db_connect() as db:
        agent_id = db.execute("SELECT id FROM agents WHERE name='Test Codex'").fetchone()["id"]
    # Create proposal assigned to codex agent
    pid = client.post("/api/proposals", data={
        "title": "Codex task", "assigned_agent_id": agent_id
    }).json()["id"]
    executor_file = tmp_path / "proposals_trigger_executor"
    assert executor_file.exists()
    data = json.loads(executor_file.read_text())
    assert data["executor_type"] == "codex"
    assert data["proposal_id"] == pid

    # Hermes agent should NOT write executor file
    executor_file.unlink()
    pid2 = client.post("/api/proposals", data={
        "title": "Hermes task", "assigned_agent_id": "agent_builder"
    }).json()["id"]
    assert not executor_file.exists()
```

**Step 5: Test YOLO safety policy**

```python
def test_dangerous_executor_triggers_approval_on_status_change(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    # Create codex agent and proposal
    client.post("/api/agents", data={
        "name": "YOLO Coder", "role_title": "Coder",
        "provider": "openai", "model_name": "gpt-5",
        "executor_type": "codex", "tools_allowed": "comment",
        "monthly_budget_usd": "50",
    })
    with main.db_connect() as db:
        agent_id = db.execute("SELECT id FROM agents WHERE name='YOLO Coder'").fetchone()["id"]
    pid = client.post("/api/proposals", data={
        "title": "Risky task", "assigned_agent_id": agent_id
    }).json()["id"]
    # Approve the card
    client.patch(f"/api/proposals/{pid}/status", data={"status": "approved"})
    with main.db_connect() as db:
        approvals = db.execute(
            "SELECT title FROM approval_requests WHERE entity_type='proposal' AND entity_id=?",
            (pid,)
        ).fetchall()
        titles = {r["title"] for r in approvals}
        assert "Dangerous executor (Codex CLI) requires approval" in titles
```

**Step 6: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
# Expected: 17 passed (12 existing + 5 new)
```

**Step 7: Commit**

```bash
git add tests/test_agent_ops.py
git commit -m "test: add executor column, template, API, trigger, and safety policy tests"
```

---

### Task 7: Dry-run mode — "Test Executor" card template

**Objective:** A one-click button in the Setup page that creates a test proposal assigned to a CLI agent, so users can verify the full pipeline works.

**Files:**
- Modify: `main.py` (new route)
- Modify: `templates/setup.html` (button)

**Step 1: Add "Test" button to setup agent sidebar**

In setup.html, in the side-list for each agent, add:

```html
{% set exec = agent.get('executor_type', 'hermes') %}
{% if exec != 'hermes' %}
<form action="/api/proposals/dry-run" method="post" style="display:inline">
  <input type="hidden" name="agent_id" value="{{ agent.id }}">
  <input type="hidden" name="return_to" value="/proposals/setup?view=org&agent_id={{ agent.id }}">
  <button class="btn btn-sm" title="Create a test card to verify the {{ exec }} CLI pipeline">Test {{ exec }}</button>
</form>
{% endif %}
```

**Step 2: Add dry-run endpoint**

```python
@app.post("/api/proposals/dry-run")
async def create_dry_run_proposal(agent_id: str = Form(...), return_to: str = Form("")):
    with db_connect() as db:
        agent = row(db.execute("SELECT id, name, executor_type FROM agents WHERE id=?", (agent_id,)))
        if not agent:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        executor_type = agent.get("executor_type", "hermes")
        if executor_type == "hermes":
            return JSONResponse({"error": "agent uses native Hermes, no dry-run needed"}, status_code=400)

    title = f"[DRY-RUN] Test {EXECUTOR_LABELS.get(executor_type, executor_type)} pipeline"
    body = f"Dry-run verification for agent '{agent['name']}' ({executor_type}).\n\nThis card verifies the full pipeline: trigger file → executor spawn → diff review → test run → kanban_complete.\n\nNo production changes should be made. Expected output: 'DRY_RUN_OK'."

    pid = make_id("p")
    now = ts()
    with db_connect() as db:
        db.execute(
            "INSERT INTO proposals (id,title,body,status,board,assigned_agent_id,created_at,updated_at) VALUES (?,?,?,'processing','default',?,?,?)",
            (pid, title, body, agent_id, now, now),
        )
        create_event(db, "human", "user", "proposal", pid, "dry_run_created", {"agent_id": agent_id, "executor_type": executor_type})
        write_trigger_executor_meta(db, pid)
        db.commit()
    TRIGGER_FILE.write_text(pid)
    return RedirectResponse(safe_return_path(return_to, f"/proposals/{pid}"), status_code=303)
```

**Step 3: Commit**

```bash
git add main.py templates/setup.html
git commit -m "feat: add dry-run Test button for CLI executor pipeline verification"
```

---

### Task 8: Final audit — compile, test, verify

**Objective:** Confirm everything works end-to-end.

**Step 1: Compile check**

```bash
.venv/bin/python -m compileall -q main.py
# Expected: silent (no output = no errors)
```

**Step 2: Full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
# Expected: all tests pass
```

**Step 3: Manual API smoke test**

```bash
# Start dev server
HERMES_REQUIRE_AUTH=0 .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8089 &

# Test executor endpoint
curl -s http://127.0.0.1:8089/api/agents/agent_builder/executor-status | python3 -m json.tool
# Expected: {"agent_id":"agent_builder","executor_type":"hermes","status":"native","ready":true}

# Test health check
curl -s http://127.0.0.1:8089/health
# Expected: {"status":"healthy","database":"ok"}

# Kill server
kill %1
```

**Step 4: Review the diff**

```bash
git diff --stat
# Should show: main.py, templates/agent_detail.html, templates/agents.html, templates/setup.html, tests/test_agent_ops.py
```

**Step 5: Commit**

```bash
git add -A
git commit -m "chore: final audit — all executor gaps closed, tests pass"
```

---

## Gap Coverage Summary

| Gap | Task | Status |
|-----|------|--------|
| No executor cost tracking | Task 1 | Planned |
| No CLI health check | Task 2 | Planned |
| No Verify CLI button in UI | Task 3 | Planned |
| No --yolo safety approval | Task 4 | Planned |
| No Node.js guard for cmd | Task 5 | Planned |
| No executor tests | Task 6 | Planned |
| No dry-run pipeline test | Task 7 | Planned |
| Docs already complete | — | Done (cli-executor-reference.md) |
| Skills already verified | — | Done (review pass) |
| Trigger file working | — | Done (verified earlier) |
| API endpoint working | — | Done (verified earlier) |

## Files Changed

```
main.py                              (+~120 lines)
templates/agent_detail.html          (+~30 lines)
templates/agents.html                (+~10 lines)
templates/setup.html                 (+~10 lines)
tests/test_agent_ops.py              (+~120 lines)
```

No new files. All changes are additive within existing files. No breaking changes to routes or schema.
