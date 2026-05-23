import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_HOME.mkdir(parents=True, exist_ok=True)
PROPOSALS_DB = HERMES_HOME / "proposals.db"
TRIGGER_FILE = HERMES_HOME / "proposals_trigger"
TRIGGER_EXECUTOR_FILE = HERMES_HOME / "proposals_trigger_executor"
PROFILES_DIR = HERMES_HOME / "profiles"

AUTH_URL = os.environ.get("AUTH_URL", "https://reidar.tech")
HERMES_REQUIRE_AUTH = os.environ.get("HERMES_REQUIRE_AUTH", "1").lower() not in {"0", "false", "no"}
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "hermes-local")

PROPOSAL_STATUSES = ["processing", "review", "approved", "implemented", "rejected"]
PROPOSAL_LABELS = {
    "processing": "Analyzing...",
    "review": "In Review",
    "approved": "Approved",
    "implemented": "Done",
    "rejected": "Rejected",
}
RISK_LEVELS = ["low", "medium", "high", "critical"]
AGENT_STATUSES = ["active", "paused", "disabled"]
EXECUTOR_TYPES = ["hermes", "codex", "claude-code", "opencode", "agy", "command-code", "kilo"]
EXECUTOR_LABELS = {
    "hermes": "Hermes (native)",
    "codex": "Codex CLI",
    "claude-code": "Claude Code CLI",
    "opencode": "OpenCode CLI",
    "agy": "Antigravity CLI",
    "command-code": "Command Code CLI",
    "kilo": "Kilo Code CLI",
}
GOAL_STATUSES = ["planned", "active", "blocked", "done", "dropped"]
GOAL_PRIORITIES = ["low", "medium", "high", "critical"]
WORKFLOW_RUN_STATUSES = ["running", "completed", "failed", "paused"]
HANDOFF_STATUSES = ["requested", "accepted", "rejected", "completed"]
APPROVAL_STATUSES = ["pending", "approved", "rejected"]

AGENT_EMOJI = {
    "orchestrator": "*",
    "coder": ">",
    "researcher": "?",
    "reviewer": "!",
    "product": "P",
    "architect": "A",
    "builder": "B",
    "qa": "Q",
    "cost": "$",
}
AGENT_COLORS = {
    "orchestrator": "#58a6ff",
    "coder": "#3fb950",
    "researcher": "#d2991d",
    "reviewer": "#a371f7",
    "product": "#58a6ff",
    "architect": "#a371f7",
    "builder": "#3fb950",
    "qa": "#d2991d",
    "cost": "#f85149",
}

AGENT_TEMPLATE_DEFS = {
    "product_lead": {
        "name": "Product Lead",
        "role_title": "Product Strategist",
        "purpose": "Turns goals into epics and acceptance criteria.",
        "system_prompt": "Clarify outcomes, create structured backlog, and avoid code changes.",
        "provider": "openai",
        "model_name": "gpt-4.1",
        "tools_allowed": ["comment", "create_goal", "create_card"],
        "monthly_budget_usd": 25,
        "executor_type": "hermes",
    },
    "architect": {
        "name": "Architect",
        "role_title": "Technical Architect",
        "purpose": "Breaks goals into implementation plans and risks.",
        "system_prompt": "Produce pragmatic plans, dependencies, and risks.",
        "provider": "openai",
        "model_name": "gpt-4.1",
        "tools_allowed": ["comment", "create_subtask", "handoff"],
        "monthly_budget_usd": 25,
        "executor_type": "hermes",
    },
    "builder": {
        "name": "Builder",
        "role_title": "Implementation Agent",
        "purpose": "Implements approved tasks and reports changes.",
        "system_prompt": "Work in small reviewed changes and ask for approval before risky operations.",
        "provider": "openai",
        "model_name": "gpt-4.1",
        "tools_allowed": ["comment", "propose_patch"],
        "monthly_budget_usd": 50,
        "executor_type": "hermes",
    },
    "reviewer": {
        "name": "Reviewer",
        "role_title": "Code Reviewer",
        "purpose": "Reviews output for quality, risks, and acceptance criteria.",
        "system_prompt": "Prioritize bugs, regressions, and missing tests.",
        "provider": "openai",
        "model_name": "gpt-4.1",
        "tools_allowed": ["comment", "request_changes", "handoff"],
        "monthly_budget_usd": 20,
        "executor_type": "hermes",
    },
    "qa": {
        "name": "QA Agent",
        "role_title": "Quality Analyst",
        "purpose": "Creates test plans and validates acceptance criteria.",
        "system_prompt": "Turn acceptance criteria into focused test scenarios.",
        "provider": "openai",
        "model_name": "gpt-4.1",
        "tools_allowed": ["comment", "create_test_plan"],
        "monthly_budget_usd": 15,
        "executor_type": "hermes",
    },
    "cost_controller": {
        "name": "Cost Controller",
        "role_title": "Cost Controller",
        "purpose": "Watches spend and flags runaway work.",
        "system_prompt": "Warn on cost overruns and pause expensive loops.",
        "provider": "manual",
        "model_name": "manual",
        "tools_allowed": ["comment", "request_approval"],
        "monthly_budget_usd": 10,
        "executor_type": "hermes",
    },
    # --- CLI Delegator Templates ---
    "codex_coder": {
        "name": "Codex Coder",
        "role_title": "Codex Delegator",
        "purpose": "Delegates implementation to OpenAI Codex CLI in isolated worktrees.",
        "system_prompt": "Delegate coding tasks to Codex CLI. Use 'codex exec --full-auto' for safe sandboxed one-shot tasks, 'codex exec --ask-for-approval never' for fully unattended. Never trust Codex self-report — always verify the diff and run tests independently. Spawn in an isolated worktree, review changes, run tests, report via kanban_complete.",
        "provider": "openai",
        "model_name": "gpt-5",
        "tools_allowed": ["comment", "kanban_complete", "kanban_heartbeat", "kanban_block"],
        "monthly_budget_usd": 75,
        "executor_type": "codex",
    },
    "claude_coder": {
        "name": "Claude Coder",
        "role_title": "Claude Code Delegator",
        "purpose": "Delegates implementation to Claude Code CLI in isolated worktrees.",
        "system_prompt": "Delegate coding tasks to Claude Code CLI. Use 'claude -p' (print mode) for one-shot non-interactive tasks, 'claude -p --dangerously-skip-permissions --max-turns 20' for fully unattended. Spawn in an isolated worktree, review the diff, run tests, and report via kanban_complete.",
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "tools_allowed": ["comment", "kanban_complete", "kanban_heartbeat", "kanban_block"],
        "monthly_budget_usd": 75,
        "executor_type": "claude-code",
    },
    "opencode_coder": {
        "name": "OpenCode Coder",
        "role_title": "OpenCode Delegator",
        "purpose": "Delegates implementation to OpenCode CLI (provider-agnostic).",
        "system_prompt": "Delegate coding tasks to OpenCode CLI. Use 'opencode run' for one-shot tasks, interactive PTY for multi-turn. Construct prompts with safety constraints, spawn in an isolated worktree, review the diff, run tests, and report via kanban_complete.",
        "provider": "openrouter",
        "model_name": "auto",
        "tools_allowed": ["comment", "kanban_complete", "kanban_heartbeat", "kanban_block"],
        "monthly_budget_usd": 75,
        "executor_type": "opencode",
    },
    "agy_coder": {
        "name": "Antigravity Coder",
        "role_title": "Antigravity Delegator",
        "purpose": "Delegates implementation to Google Antigravity CLI (agy).",
        "system_prompt": "Delegate coding tasks to Antigravity CLI. Use 'agy exec' for one-shot, background PTY for long tasks. Construct prompts with safety constraints, spawn in an isolated worktree, review the diff, run tests, and report via kanban_complete.",
        "provider": "google",
        "model_name": "gemini-3-flash-preview",
        "tools_allowed": ["comment", "kanban_complete", "kanban_heartbeat", "kanban_block"],
        "monthly_budget_usd": 50,
        "executor_type": "agy",
    },
    "commandcode_coder": {
        "name": "CommandCode Coder",
        "role_title": "Command Code Delegator",
        "purpose": "Delegates implementation to Command Code CLI (cmd) with taste-1 style learning.",
        "system_prompt": "Delegate coding tasks to Command Code CLI. Binary is 'cmd' (npm: command-code). Use 'cmd -p' for headless one-shot tasks, 'cmd -p --yolo' for fully unattended. Command Code learns coding style (taste-1) from accepts/rejects. Spawn in an isolated worktree, review the diff, run tests, and report via kanban_complete. Requires Node.js 20+.",
        "provider": "openai",
        "model_name": "gpt-5",
        "tools_allowed": ["comment", "kanban_complete", "kanban_heartbeat", "kanban_block"],
        "monthly_budget_usd": 75,
        "executor_type": "command-code",
    },
    "kilo_coder": {
        "name": "Kilo Coder",
        "role_title": "Kilo Code Delegator",
        "purpose": "Delegates implementation to Kilo Code CLI (kilo) — open source, 500+ models.",
        "system_prompt": "Delegate coding tasks to Kilo Code CLI. Binary is 'kilo' (npm: @kilocode/cli). Use 'kilo run --auto' for autonomous CI/CD mode, 'kilo run' for one-shot. Fork of OpenCode, same config format. Spawn in an isolated worktree, review the diff, run tests, and report via kanban_complete. Apache-2.0 open source.",
        "provider": "openrouter",
        "model_name": "auto",
        "tools_allowed": ["comment", "kanban_complete", "kanban_heartbeat", "kanban_block"],
        "monthly_budget_usd": 75,
        "executor_type": "kilo",
    },
}

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ts() -> int:
    return int(time.time())


def month_start_ts(now: int | None = None) -> int:
    current = time.localtime(now or ts())
    return int(time.mktime((current.tm_year, current.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def money(value: Any) -> str:
    try:
        return f"${float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def lines_to_json(value: str | None) -> str:
    items = [line.strip() for line in (value or "").splitlines() if line.strip()]
    return dumps(items)


def json_to_lines(value: str | None) -> str:
    return "\n".join(loads(value, []) or [])


def safe_return_path(value: str | None, default: str) -> str:
    if value and value.startswith("/proposals"):
        return value
    return default


def write_trigger_executor_meta(db: sqlite3.Connection, proposal_id: str) -> None:
    """Write executor metadata alongside the trigger file so external consumers
    know which CLI to spawn without parsing the trigger file format."""
    proposal = row(db.execute("SELECT id, assigned_agent_id FROM proposals WHERE id=?", (proposal_id,)))
    if not proposal or not proposal.get("assigned_agent_id"):
        return
    agent = row(db.execute("SELECT id, executor_type FROM agents WHERE id=?", (proposal["assigned_agent_id"],)))
    if not agent:
        return
    executor_type = agent.get("executor_type", "hermes")
    if executor_type == "hermes":
        return  # native Hermes execution — no need to write extra metadata
    TRIGGER_EXECUTOR_FILE.write_text(dumps({
        "proposal_id": proposal_id,
        "agent_id": agent["id"],
        "executor_type": executor_type,
        "executor_label": EXECUTOR_LABELS.get(executor_type, executor_type),
    }))


def get_profiles() -> list[str]:
    if PROFILES_DIR.is_dir():
        return sorted(d.name for d in PROFILES_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists())
    return []


def get_node_version() -> tuple[int, int] | None:
    """Return (major, minor) or None if node not found."""
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


def db_connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(PROPOSALS_DB))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def rows(result: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in result.fetchall()]


def row(result: sqlite3.Cursor) -> dict[str, Any] | None:
    found = result.fetchone()
    return dict(found) if found else None


def ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_event(
    db: sqlite3.Connection,
    actor_type: str,
    actor_id: str,
    entity_type: str,
    entity_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO audit_events
        (id, workspace_id, actor_type, actor_id, entity_type, entity_id, event_type, payload_json, created_at)
        VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?)
        """,
        (make_id("evt"), actor_type, actor_id, entity_type, entity_id, event_type, dumps(payload or {}), ts()),
    )


def create_approval(
    db: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    title: str,
    risk_level: str,
    reason: str,
    requested_by_type: str = "system",
    requested_by_id: str = "policy",
    payload: dict[str, Any] | None = None,
) -> str:
    existing = db.execute(
        """
        SELECT id FROM approval_requests
        WHERE entity_type=? AND entity_id=? AND title=? AND status='pending'
        """,
        (entity_type, entity_id, title),
    ).fetchone()
    if existing:
        return str(existing["id"])
    aid = make_id("appr")
    now = ts()
    db.execute(
        """
        INSERT INTO approval_requests
        (id, policy_id, entity_type, entity_id, title, risk_level, reason, requested_by_type,
         requested_by_id, status, payload_json, created_at, updated_at)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            aid,
            entity_type,
            entity_id,
            title,
            risk_level,
            reason,
            requested_by_type,
            requested_by_id,
            dumps(payload or {}),
            now,
            now,
        ),
    )
    create_event(db, "system", "policy", entity_type, entity_id, "approval_requested", {"approval_id": aid, "reason": reason})
    return aid


def ensure_policy_approvals(db: sqlite3.Connection, proposal_id: str) -> None:
    proposal = row(db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)))
    if not proposal:
        return
    total_cost = card_cost(db, proposal_id)
    if proposal.get("risk_level") == "critical":
        create_approval(
            db,
            "proposal",
            proposal_id,
            "Critical-risk card requires approval",
            "critical",
            "Default policy requires human approval for critical-risk cards.",
            payload={"risk_level": "critical"},
        )
    if total_cost > 2.0:
        create_approval(
            db,
            "proposal",
            proposal_id,
            "Cost threshold exceeded",
            "high",
            "Default policy requires approval when estimated/manual card cost exceeds $2.00.",
            payload={"estimated_total_cost_usd": total_cost},
        )

    # Dangerous executor safety policy
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


def create_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'processing',
            author TEXT NOT NULL DEFAULT 'user',
            board TEXT NOT NULL DEFAULT 'default',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS proposal_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            parent_id INTEGER,
            created_at INTEGER NOT NULL
        )
        """
    )
    for column, definition in {
        "goal_id": "TEXT",
        "parent_id": "TEXT",
        "assigned_agent_id": "TEXT",
        "acceptance_criteria_json": "TEXT NOT NULL DEFAULT '[]'",
        "risk_level": "TEXT NOT NULL DEFAULT 'low'",
        "estimated_cost_usd": "REAL NOT NULL DEFAULT 0",
        "actual_cost_usd": "REAL NOT NULL DEFAULT 0",
    }.items():
        ensure_column(db, "proposals", column, definition)
    ensure_column(db, "proposal_comments", "parent_id", "INTEGER")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role_title TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT 'manual',
            model_name TEXT NOT NULL DEFAULT 'manual',
            executor_type TEXT NOT NULL DEFAULT 'hermes',
            tools_allowed_json TEXT NOT NULL DEFAULT '[]',
            monthly_budget_usd REAL NOT NULL DEFAULT 0,
            manager_agent_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    ensure_column(db, "agents", "executor_type", "TEXT NOT NULL DEFAULT 'hermes'")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            desired_outcome TEXT NOT NULL DEFAULT '',
            success_metric TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'medium',
            owner_type TEXT NOT NULL DEFAULT 'human',
            owner_id TEXT NOT NULL DEFAULT 'user',
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_template_stages (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            role_hint TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            assigned_agent_id TEXT,
            handoff_agent_id TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    ensure_column(db, "workflow_template_stages", "assigned_agent_id", "TEXT")
    ensure_column(db, "workflow_template_stages", "handoff_agent_id", "TEXT")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            proposal_id TEXT,
            goal_id TEXT,
            current_stage_id TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            started_by TEXT NOT NULL DEFAULT 'user',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            completed_at INTEGER
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_run_stages (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            template_stage_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent_id TEXT,
            handoff_agent_id TEXT,
            started_at INTEGER,
            completed_at INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    ensure_column(db, "workflow_run_stages", "created_at", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "workflow_run_stages", "assigned_agent_id", "TEXT")
    ensure_column(db, "workflow_run_stages", "handoff_agent_id", "TEXT")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_handoffs (
            id TEXT PRIMARY KEY,
            from_agent_id TEXT,
            to_agent_id TEXT,
            proposal_id TEXT,
            goal_id TEXT,
            workflow_run_id TEXT,
            reason TEXT NOT NULL DEFAULT '',
            context_summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'requested',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_records (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            agent_run_id TEXT,
            provider TEXT NOT NULL DEFAULT 'manual',
            model TEXT NOT NULL DEFAULT 'manual',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            actual_cost_usd REAL NOT NULL DEFAULT 0,
            manual_note TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        )
        """
    )
    ensure_column(db, "usage_records", "actual_cost_usd", "REAL NOT NULL DEFAULT 0")
    ensure_column(db, "usage_records", "executor_type", "TEXT NOT NULL DEFAULT ''")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS budgets (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            period TEXT NOT NULL DEFAULT 'monthly',
            limit_usd REAL NOT NULL,
            behavior_on_limit TEXT NOT NULL DEFAULT 'warn',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_policies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            condition_json TEXT NOT NULL DEFAULT '{}',
            action TEXT NOT NULL DEFAULT 'require_approval',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_requests (
            id TEXT PRIMARY KEY,
            policy_id TEXT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            title TEXT NOT NULL,
            risk_level TEXT NOT NULL DEFAULT 'medium',
            reason TEXT NOT NULL DEFAULT '',
            requested_by_type TEXT NOT NULL DEFAULT 'system',
            requested_by_id TEXT NOT NULL DEFAULT 'policy',
            status TEXT NOT NULL DEFAULT 'pending',
            payload_json TEXT NOT NULL DEFAULT '{}',
            decision_reason TEXT NOT NULL DEFAULT '',
            decided_by TEXT,
            decided_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL DEFAULT 'default',
            actor_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_prop_status ON proposals(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_prop_goal ON proposals(goal_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_prop_agent ON proposals(assigned_agent_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_pc_proposal ON proposal_comments(proposal_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_usage_scope ON usage_records(scope_type, scope_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_budget_scope ON budgets(scope_type, scope_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status, risk_level)")


def seed_defaults(db: sqlite3.Connection) -> None:
    now = ts()
    agents = [
        ("agent_product_lead", AGENT_TEMPLATE_DEFS["product_lead"], None),
        ("agent_architect", AGENT_TEMPLATE_DEFS["architect"], "agent_product_lead"),
        ("agent_builder", AGENT_TEMPLATE_DEFS["builder"], "agent_architect"),
        ("agent_reviewer", AGENT_TEMPLATE_DEFS["reviewer"], "agent_architect"),
        ("agent_qa", AGENT_TEMPLATE_DEFS["qa"], "agent_reviewer"),
        ("agent_cost", AGENT_TEMPLATE_DEFS["cost_controller"], None),
    ]
    for agent_id, agent, manager_agent_id in agents:
        db.execute(
            """
            INSERT OR IGNORE INTO agents
            (id, name, role_title, purpose, system_prompt, provider, model_name, executor_type, tools_allowed_json,
             monthly_budget_usd, manager_agent_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                agent_id,
                agent["name"],
                agent["role_title"],
                agent["purpose"],
                agent["system_prompt"],
                agent["provider"],
                agent["model_name"],
                agent.get("executor_type", "hermes"),
                dumps(agent["tools_allowed"]),
                agent["monthly_budget_usd"],
                manager_agent_id,
                now,
                now,
            ),
        )

    templates_data = [
        ("workflow_feature_delivery", "Feature Delivery", "Plan, build, review, and verify a feature.", ["Product brief", "Architecture", "Build", "Review", "QA", "Done"]),
        ("workflow_bug_triage", "Bug Triage", "Reproduce, classify, fix, and verify a bug.", ["Reproduce", "Impact", "Fix plan", "Patch", "Verify"]),
        ("workflow_research", "Research", "Gather context, compare options, and produce a recommendation.", ["Question", "Sources", "Synthesis", "Recommendation"]),
    ]
    for template_id, name, description, stages in templates_data:
        db.execute(
            """
            INSERT OR IGNORE INTO workflow_templates (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (template_id, name, description, now, now),
        )
        for position, stage_name in enumerate(stages, 1):
            stage_id = f"{template_id}_stage_{position}"
            db.execute(
                """
                INSERT OR IGNORE INTO workflow_template_stages
                (id, template_id, position, name, role_hint, description, created_at)
                VALUES (?, ?, ?, ?, '', '', ?)
                """,
                (stage_id, template_id, position, stage_name, now),
            )

    policies = [
        ("policy_critical_card", "Critical-risk cards require approval", {"entity_type": "proposal", "risk_level": "critical"}, "require_approval"),
        ("policy_cost_threshold", "Over-threshold cost requires approval", {"estimated_cost_usd_gt": 2.0}, "require_approval"),
        ("policy_failed_workflow", "Failed-stage workflow completion requires approval", {"workflow_failed_stage": True}, "require_approval"),
        ("policy_dangerous_executor", "Dangerous-executor cards require approval", {"entity_type": "proposal", "executor_dangerous": True}, "require_approval"),
    ]
    for policy_id, name, condition, action in policies:
        db.execute(
            """
            INSERT OR IGNORE INTO approval_policies
            (id, name, condition_json, action, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (policy_id, name, dumps(condition), action, now, now),
        )


def init_db() -> None:
    with db_connect() as db:
        create_schema(db)
        seed_defaults(db)
        db.commit()


def usage_total(db: sqlite3.Connection, scope_type: str, scope_id: str) -> float:
    value = db.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM usage_records WHERE scope_type=? AND scope_id=?",
        (scope_type, scope_id),
    ).fetchone()["total"]
    return float(value or 0)


def usage_actual_total(db: sqlite3.Connection, scope_type: str, scope_id: str, since_ts: int | None = None) -> float:
    params: list[Any] = [scope_type, scope_id]
    clause = ""
    if since_ts is not None:
        clause = " AND created_at>=?"
        params.append(since_ts)
    value = db.execute(
        f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS total FROM usage_records WHERE scope_type=? AND scope_id=?{clause}",
        params,
    ).fetchone()["total"]
    return float(value or 0)


def proposal_actual_total(db: sqlite3.Connection, where: str = "", params: tuple[Any, ...] = ()) -> float:
    query = "SELECT COALESCE(SUM(actual_cost_usd), 0) AS total FROM proposals"
    if where:
        query += f" WHERE {where}"
    value = db.execute(query, params).fetchone()["total"]
    return float(value or 0)


def card_cost(db: sqlite3.Connection, proposal_id: str) -> float:
    p = db.execute(
        "SELECT estimated_cost_usd, actual_cost_usd FROM proposals WHERE id=?",
        (proposal_id,),
    ).fetchone()
    if not p:
        return 0.0
    return float(p["estimated_cost_usd"] or 0) + float(p["actual_cost_usd"] or 0) + usage_total(db, "proposal", proposal_id)


def scope_spend(db: sqlite3.Connection, scope_type: str, scope_id: str) -> float:
    if scope_type == "workspace":
        p_total = proposal_actual_total(db)
        u_total = db.execute("SELECT COALESCE(SUM(actual_cost_usd), 0) AS total FROM usage_records").fetchone()["total"]
        return float(p_total or 0) + float(u_total or 0)
    if scope_type == "goal":
        p_total = proposal_actual_total(db, "goal_id=?", (scope_id,))
        return float(p_total or 0) + usage_actual_total(db, scope_type, scope_id)
    if scope_type == "agent":
        p_total = proposal_actual_total(db, "assigned_agent_id=?", (scope_id,))
        return float(p_total or 0) + usage_actual_total(db, scope_type, scope_id)
    if scope_type == "project":
        p_total = proposal_actual_total(db, "board=?", (scope_id,))
        return float(p_total or 0) + usage_actual_total(db, scope_type, scope_id)
    return usage_actual_total(db, scope_type, scope_id)


def agent_cost_summary(db: sqlite3.Connection, agent_id: str) -> dict[str, float | int]:
    current_month = month_start_ts()
    assigned_actual = proposal_actual_total(db, "assigned_agent_id=?", (agent_id,))
    assigned_estimated = db.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM proposals WHERE assigned_agent_id=?",
        (agent_id,),
    ).fetchone()["total"]
    usage_actual = usage_actual_total(db, "agent", agent_id)
    usage_actual_month = usage_actual_total(db, "agent", agent_id, current_month)
    usage_estimated = usage_total(db, "agent", agent_id)
    usage_count = db.execute(
        "SELECT COUNT(*) AS total FROM usage_records WHERE scope_type='agent' AND scope_id=?",
        (agent_id,),
    ).fetchone()["total"]
    return {
        "actual_spend_usd": float(assigned_actual or 0) + usage_actual,
        "monthly_actual_spend_usd": usage_actual_month,
        "estimated_spend_usd": float(assigned_estimated or 0) + usage_estimated,
        "usage_record_count": int(usage_count or 0),
    }


def enrich_agents_for_setup(db: sqlite3.Connection) -> list[dict[str, Any]]:
    agents = rows(
        db.execute(
            """
            SELECT a.*, m.name AS manager_name
            FROM agents a
            LEFT JOIN agents m ON m.id=a.manager_agent_id
            ORDER BY a.name
            """
        )
    )
    for agent in agents:
        agent["tools"] = loads(agent.get("tools_allowed_json"), []) or []
        agent.update(agent_cost_summary(db, agent["id"]))
        monthly_budget = float(agent.get("monthly_budget_usd") or 0)
        agent["budget_percent"] = min(100, round((float(agent["monthly_actual_spend_usd"] or 0) / monthly_budget) * 100, 2)) if monthly_budget else 0
    return agents


def build_org_levels(agents: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_id = {agent["id"]: agent for agent in agents}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for agent in agents:
        manager_id = agent.get("manager_agent_id")
        if manager_id and manager_id not in by_id:
            manager_id = None
        children.setdefault(manager_id, []).append(agent)
    for sibling_group in children.values():
        sibling_group.sort(key=lambda item: (item.get("role_title") or "", item.get("name") or ""))

    levels: list[list[dict[str, Any]]] = []
    current = children.get(None, [])
    visited: set[str] = set()
    while current:
        levels.append(current)
        next_level: list[dict[str, Any]] = []
        for agent in current:
            visited.add(agent["id"])
            next_level.extend(children.get(agent["id"], []))
        current = [agent for agent in next_level if agent["id"] not in visited]

    remaining = [agent for agent in agents if agent["id"] not in visited]
    if remaining:
        levels.append(remaining)
    return levels


def workflow_templates_for_setup(db: sqlite3.Connection) -> list[dict[str, Any]]:
    templates_list = rows(db.execute("SELECT * FROM workflow_templates ORDER BY name"))
    for template in templates_list:
        template["stages"] = rows(
            db.execute(
                """
                SELECT s.*, aa.name AS assigned_agent_name, ha.name AS handoff_agent_name
                FROM workflow_template_stages s
                LEFT JOIN agents aa ON aa.id=s.assigned_agent_id
                LEFT JOIN agents ha ON ha.id=s.handoff_agent_id
                WHERE s.template_id=?
                ORDER BY s.position
                """,
                (template["id"],),
            )
        )
    return templates_list


def normalize_stage_positions(db: sqlite3.Connection, template_id: str) -> None:
    stages = rows(db.execute("SELECT id FROM workflow_template_stages WHERE template_id=? ORDER BY position, created_at", (template_id,)))
    for index, stage in enumerate(stages, 1):
        db.execute("UPDATE workflow_template_stages SET position=? WHERE id=?", (index, stage["id"]))


def budget_rows(db: sqlite3.Connection, scope_type: str | None = None, scope_id: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    query = "SELECT * FROM budgets"
    clauses = []
    if scope_type:
        clauses.append("scope_type=?")
        params.append(scope_type)
    if scope_id:
        clauses.append("scope_id=?")
        params.append(scope_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY scope_type, scope_id"
    data = rows(db.execute(query, params))
    for budget in data:
        budget["spent_usd"] = scope_spend(db, budget["scope_type"], budget["scope_id"])
        budget["is_over"] = budget["spent_usd"] > float(budget["limit_usd"] or 0)
    return data


def enrich_proposals(db: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    data = rows(db.execute(query, params))
    for p in data:
        p["goal"] = row(db.execute("SELECT id, title FROM goals WHERE id=?", (p.get("goal_id"),))) if p.get("goal_id") else None
        p["agent"] = row(db.execute("SELECT id, name, role_title, status FROM agents WHERE id=?", (p.get("assigned_agent_id"),))) if p.get("assigned_agent_id") else None
        p["parent"] = row(db.execute("SELECT id, title FROM proposals WHERE id=?", (p.get("parent_id"),))) if p.get("parent_id") else None
        p["criteria"] = loads(p.get("acceptance_criteria_json"), []) or []
        p["cost_total"] = card_cost(db, p["id"])
    return data


def entity_events(db: sqlite3.Connection, entity_type: str, entity_id: str, limit: int = 50) -> list[dict[str, Any]]:
    data = rows(
        db.execute(
            """
            SELECT * FROM audit_events
            WHERE entity_type=? AND entity_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (entity_type, entity_id, limit),
        )
    )
    for event in data:
        event["payload"] = loads(event.get("payload_json"), {}) or {}
    return data


def proposal_context(db: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    proposal_rows = enrich_proposals(db, "SELECT * FROM proposals WHERE id=?", (proposal_id,))
    if not proposal_rows:
        return None
    proposal = proposal_rows[0]
    comments = rows(db.execute("SELECT * FROM proposal_comments WHERE proposal_id=? ORDER BY created_at ASC", (proposal_id,)))
    return {
        "proposal": proposal,
        "comments": comments,
        "agents": rows(db.execute("SELECT * FROM agents ORDER BY name")),
        "goals": rows(db.execute("SELECT * FROM goals ORDER BY updated_at DESC")),
        "parents": rows(db.execute("SELECT id, title FROM proposals WHERE id<>? ORDER BY updated_at DESC LIMIT 100", (proposal_id,))),
        "events": entity_events(db, "proposal", proposal_id),
        "handoffs": rows(db.execute("SELECT h.*, fa.name AS from_agent, ta.name AS to_agent FROM agent_handoffs h LEFT JOIN agents fa ON fa.id=h.from_agent_id LEFT JOIN agents ta ON ta.id=h.to_agent_id WHERE h.proposal_id=? ORDER BY h.created_at DESC", (proposal_id,))),
        "workflow_runs": rows(db.execute("SELECT wr.*, wt.name AS template_name FROM workflow_runs wr JOIN workflow_templates wt ON wt.id=wr.template_id WHERE wr.proposal_id=? ORDER BY wr.updated_at DESC", (proposal_id,))),
        "approvals": rows(db.execute("SELECT * FROM approval_requests WHERE entity_type='proposal' AND entity_id=? ORDER BY created_at DESC", (proposal_id,))),
        "budgets": budget_rows(db, "proposal", proposal_id),
        "executor": get_executor_for_proposal(db, proposal_id),
    }


def get_executor_for_proposal(db: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    """Return executor routing info for a proposal's assigned agent, or None if native Hermes."""
    proposal = row(db.execute("SELECT assigned_agent_id FROM proposals WHERE id=?", (proposal_id,)))
    if not proposal or not proposal.get("assigned_agent_id"):
        return None
    agent = row(db.execute("SELECT id, name, executor_type FROM agents WHERE id=?", (proposal["assigned_agent_id"],)))
    if not agent:
        return None
    executor_type = agent.get("executor_type", "hermes")
    if executor_type == "hermes":
        return None
    return {
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "executor_type": executor_type,
        "executor_label": EXECUTOR_LABELS.get(executor_type, executor_type),
    }


def template_context(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "PROPOSAL_LABELS": PROPOSAL_LABELS,
        "RISK_LEVELS": RISK_LEVELS,
        "AGENT_STATUSES": AGENT_STATUSES,
        "GOAL_STATUSES": GOAL_STATUSES,
        "GOAL_PRIORITIES": GOAL_PRIORITIES,
        "WORKFLOW_RUN_STATUSES": WORKFLOW_RUN_STATUSES,
        "HANDOFF_STATUSES": HANDOFF_STATUSES,
        "money": money,
        "json_to_lines": json_to_lines,
    }
    if extra:
        base.update(extra)
    return base


templates.env.globals.update(template_context())

init_db()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if not HERMES_REQUIRE_AUTH:
            return await call_next(request)
        if request.headers.get("X-Hermes-Key") == HERMES_API_KEY:
            return await call_next(request)
        token = request.cookies.get("__Secure-authjs.session-token") or request.cookies.get("authjs.session-token")
        if request.url.path.startswith("/api/"):
            if not token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        elif not token:
            return RedirectResponse(f"{AUTH_URL}/api/auth/signin")
        return await call_next(request)


app = FastAPI(title="Hermes Agent Operations Dashboard")
app.add_middleware(AuthMiddleware)


@app.get("/health")
async def health():
    try:
        with db_connect() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "healthy", "database": "ok"}
    except sqlite3.Error as exc:
        return JSONResponse({"status": "unhealthy", "database": str(exc)}, status_code=503)


@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse("/proposals", status_code=302)


@app.get("/proposals", response_class=HTMLResponse)
async def proposals_list(request: Request):
    executor_filter = request.query_params.get("executor", "")
    with db_connect() as db:
        if executor_filter == "cli":
            proposals = enrich_proposals(
                db,
                """
                SELECT p.*, (SELECT COUNT(*) FROM proposal_comments WHERE proposal_id=p.id AND parent_id IS NULL) AS top_comments
                FROM proposals p JOIN agents a ON a.id = p.assigned_agent_id
                WHERE a.executor_type != 'hermes' ORDER BY p.updated_at DESC LIMIT 100
                """,
            )
        elif executor_filter in EXECUTOR_TYPES:
            proposals = enrich_proposals(
                db,
                """
                SELECT p.*, (SELECT COUNT(*) FROM proposal_comments WHERE proposal_id=p.id AND parent_id IS NULL) AS top_comments
                FROM proposals p JOIN agents a ON a.id = p.assigned_agent_id
                WHERE a.executor_type = ? ORDER BY p.updated_at DESC LIMIT 100
                """,
                (executor_filter,),
            )
        else:
            proposals = enrich_proposals(
                db,
                """
                SELECT p.*, (SELECT COUNT(*) FROM proposal_comments WHERE proposal_id=p.id AND parent_id IS NULL) AS top_comments
                FROM proposals p ORDER BY p.updated_at DESC LIMIT 100
                """,
            )
        return templates.TemplateResponse(
            request=request,
            name="proposals_list.html",
            context=template_context({
                "proposals": proposals,
                "profiles": get_profiles(),
                "executor_filter": executor_filter,
            }),
        )


@app.get("/proposals/goals", response_class=HTMLResponse)
async def prefixed_goals_page(request: Request):
    return await goals_page(request)


@app.get("/proposals/agents", response_class=HTMLResponse)
async def prefixed_agents_page(request: Request):
    return await agents_page(request)


@app.get("/proposals/workflows", response_class=HTMLResponse)
async def prefixed_workflows_page(request: Request):
    return await workflows_page(request)


@app.get("/proposals/approvals", response_class=HTMLResponse)
async def prefixed_approvals_page(request: Request):
    return await approvals_page(request)


@app.get("/proposals/budgets", response_class=HTMLResponse)
async def prefixed_budgets_page(request: Request):
    return await budgets_page(request)


@app.get("/proposals/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    active_view = request.query_params.get("view", "org")
    if active_view not in {"org", "workflow"}:
        active_view = "org"
    selected_agent_id = request.query_params.get("agent_id")
    selected_template_id = request.query_params.get("template_id")
    selected_stage_id = request.query_params.get("stage_id")

    with db_connect() as db:
        agents = enrich_agents_for_setup(db)
        templates_list = workflow_templates_for_setup(db)

    if not selected_agent_id and agents:
        selected_agent_id = agents[0]["id"]
    selected_agent = next((agent for agent in agents if agent["id"] == selected_agent_id), None)

    if not selected_template_id and templates_list:
        selected_template_id = templates_list[0]["id"]
    selected_template = next((template for template in templates_list if template["id"] == selected_template_id), None)
    selected_stage = None
    if selected_template:
        if not selected_stage_id and selected_template["stages"]:
            selected_stage_id = selected_template["stages"][0]["id"]
        selected_stage = next((stage for stage in selected_template["stages"] if stage["id"] == selected_stage_id), None)

    context = {
        "active_view": active_view,
        "agents": agents,
        "org_levels": build_org_levels(agents),
        "agent_templates": AGENT_TEMPLATE_DEFS,
        "selected_agent": selected_agent,
        "templates_list": templates_list,
        "selected_template": selected_template,
        "selected_stage": selected_stage,
        "return_to_org": f"/proposals/setup?view=org&agent_id={selected_agent_id or ''}",
        "return_to_workflow": f"/proposals/setup?view=workflow&template_id={selected_template_id or ''}&stage_id={selected_stage_id or ''}",
    }
    return templates.TemplateResponse(request=request, name="setup.html", context=template_context(context))


@app.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def proposal_detail(request: Request, proposal_id: str):
    with db_connect() as db:
        context = proposal_context(db, proposal_id)
    if not context:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    return templates.TemplateResponse(request=request, name="proposal_detail.html", context=template_context({**context, "profiles": get_profiles()}))


@app.get("/api/proposals/{proposal_id}/executor")
async def api_proposal_executor(proposal_id: str):
    """Return executor routing info for external agent consumers.
    Returns null/None if the assigned agent uses native Hermes execution."""
    with db_connect() as db:
        result = get_executor_for_proposal(db, proposal_id)
    return {"proposal_id": proposal_id, "executor": result}


@app.get("/api/agents/{agent_id}/executor-status")
async def api_agent_executor_status(agent_id: str):
    with db_connect() as db:
        agent = row(db.execute("SELECT id, name, executor_type FROM agents WHERE id=?", (agent_id,)))
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    executor_type = agent.get("executor_type", "hermes")
    if executor_type == "hermes":
        return {"agent_id": agent_id, "executor_type": "hermes", "status": "native", "ready": True}

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

    which = shutil.which(binary)
    if not which:
        return {"agent_id": agent_id, "executor_type": executor_type, "binary": binary, "status": "not_found", "ready": False, "path": None}

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
        "version": version_output[:200],
    }


@app.get("/api/agents/executor-summary")
async def api_agents_executor_summary():
    """Return availability status for all CLI executor agents."""
    with db_connect() as db:
        agents = rows(db.execute("SELECT id, name, executor_type FROM agents WHERE executor_type != 'hermes' ORDER BY name"))

    results = {}
    for agent in agents:
        et = agent["executor_type"]
        if et not in results:
            # Check once per executor type (shared binary)
            BINARY_MAP = {
                "codex": "codex", "claude-code": "claude", "opencode": "opencode",
                "agy": "agy", "command-code": "cmd", "kilo": "kilo",
            }
            binary = BINARY_MAP.get(et)
            if binary and shutil.which(binary):
                try:
                    subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
                    results[et] = "available"
                except Exception:
                    results[et] = "error"
            else:
                results[et] = "missing"

    summary = [{"id": a["id"], "name": a["name"], "executor_type": a["executor_type"],
                "status": results.get(a["executor_type"], "unknown")} for a in agents]
    return {"agents": summary, "available_types": [et for et, s in results.items() if s == "available"],
            "missing_types": [et for et, s in results.items() if s != "available"]}


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


@app.get("/api/proposals/{proposal_id}/fragment", response_class=HTMLResponse)
async def proposal_fragment(request: Request, proposal_id: str):
    with db_connect() as db:
        context = proposal_context(db, proposal_id)
    if not context:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request=request, name="_proposal_detail_fragment.html", context=template_context(context))


@app.post("/api/proposals")
async def create_proposal(title: str = Form(...), body: str = Form(""), board: str = Form("default"), assigned_agent_id: str = Form("")):
    pid = make_id("p")
    now = ts()
    with db_connect() as db:
        db.execute(
            "INSERT INTO proposals (id,title,body,status,board,assigned_agent_id,created_at,updated_at) VALUES (?,?,?,'processing',?,NULLIF(?, ''),?,?)",
            (pid, title, body, board, assigned_agent_id, now, now),
        )
        create_event(db, "human", "user", "proposal", pid, "proposal_created", {"title": title, "board": board})
        if assigned_agent_id:
            write_trigger_executor_meta(db, pid)
        db.commit()
    TRIGGER_FILE.write_text(pid)
    return {"ok": True, "id": pid}


@app.post("/api/proposals/dry-run")
async def create_dry_run_proposal(agent_id: str = Form(...), return_to: str = Form("")):
    with db_connect() as db:
        agent = row(db.execute("SELECT id, name, executor_type FROM agents WHERE id=?", (agent_id,)))
        if not agent:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        executor_type = agent.get("executor_type", "hermes")
        if executor_type == "hermes":
            return JSONResponse({"error": "agent uses native Hermes, no dry-run needed"}, status_code=400)

    label = EXECUTOR_LABELS.get(executor_type, executor_type)
    title = f"[DRY-RUN] Test {label} pipeline"
    body = f"Dry-run verification for agent '{agent['name']}' ({executor_type}).\n\nThis card verifies the full pipeline: trigger file → executor spawn → diff review → test run.\n\nNo production changes should be made. Expected output: 'DRY_RUN_OK'."

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


@app.patch("/api/proposals/{proposal_id}/status")
async def update_proposal_status(proposal_id: str, status: str = Form(...)):
    if status not in PROPOSAL_STATUSES:
        return JSONResponse({"error": f"invalid status: {status}"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute("UPDATE proposals SET status=?, updated_at=? WHERE id=?", (status, now, proposal_id))
        create_event(db, "human", "user", "proposal", proposal_id, "proposal_status_changed", {"status": status})
        ensure_policy_approvals(db, proposal_id)
        db.commit()
    if status == "approved":
        TRIGGER_FILE.write_text(f"APPROVED:{proposal_id}")
        with db_connect() as db:
            write_trigger_executor_meta(db, proposal_id)
    return {"ok": True}


@app.post("/api/proposals/{proposal_id}/status")
async def post_proposal_status(proposal_id: str, status: str = Form(...)):
    result = await update_proposal_status(proposal_id, status)
    if isinstance(result, JSONResponse):
        return result
    return RedirectResponse(f"/proposals/{proposal_id}", status_code=303)


@app.post("/api/proposals/{proposal_id}/comments")
async def add_proposal_comment(
    proposal_id: str,
    body: str = Form(...),
    author: str = Form("agent"),
    parent_id: int | None = Form(None),
):
    now = ts()
    with db_connect() as db:
        db.execute(
            "INSERT INTO proposal_comments (proposal_id,author,body,parent_id,created_at) VALUES (?,?,?,?,?)",
            (proposal_id, author, body, parent_id, now),
        )
        db.execute("UPDATE proposals SET updated_at=? WHERE id=?", (now, proposal_id))
        create_event(db, "agent" if author != "user" else "human", author, "proposal", proposal_id, "comment_added", {"parent_id": parent_id})
        db.commit()
    return {"ok": True}


@app.post("/api/proposals/{proposal_id}/metadata")
async def update_proposal_metadata(
    proposal_id: str,
    goal_id: str = Form(""),
    parent_id: str = Form(""),
    assigned_agent_id: str = Form(""),
    risk_level: str = Form("low"),
    acceptance_criteria: str = Form(""),
    estimated_cost_usd: float = Form(0),
    actual_cost_usd: float = Form(0),
):
    if risk_level not in RISK_LEVELS:
        return JSONResponse({"error": "invalid risk_level"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            UPDATE proposals
            SET goal_id=NULLIF(?, ''), parent_id=NULLIF(?, ''), assigned_agent_id=NULLIF(?, ''),
                risk_level=?, acceptance_criteria_json=?, estimated_cost_usd=?, actual_cost_usd=?, updated_at=?
            WHERE id=?
            """,
            (
                goal_id,
                parent_id,
                assigned_agent_id,
                risk_level,
                lines_to_json(acceptance_criteria),
                estimated_cost_usd,
                actual_cost_usd,
                now,
                proposal_id,
            ),
        )
        create_event(
            db,
            "human",
            "user",
            "proposal",
            proposal_id,
            "proposal_metadata_updated",
            {"goal_id": goal_id or None, "assigned_agent_id": assigned_agent_id or None, "risk_level": risk_level},
        )
        if assigned_agent_id:
            write_trigger_executor_meta(db, proposal_id)
        ensure_policy_approvals(db, proposal_id)
        db.commit()
    return RedirectResponse(f"/proposals/{proposal_id}", status_code=303)


@app.get("/api/proposals")
async def api_proposals_list():
    with db_connect() as db:
        return enrich_proposals(db, "SELECT * FROM proposals ORDER BY updated_at DESC LIMIT 100")


@app.get("/api/proposals/{proposal_id}")
async def api_proposal_detail(proposal_id: str):
    with db_connect() as db:
        context = proposal_context(db, proposal_id)
    if not context:
        return JSONResponse({"error": "not found"}, status_code=404)
    result = context["proposal"]
    result["comments"] = context["comments"]
    result["audit_events"] = context["events"]
    result["approvals"] = context["approvals"]
    return result


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    with db_connect() as db:
        agents = rows(db.execute("SELECT * FROM agents ORDER BY name"))
        for agent in agents:
            agent["tools"] = loads(agent.get("tools_allowed_json"), []) or []
            agent["assigned_cards"] = rows(db.execute("SELECT id, title, status, risk_level FROM proposals WHERE assigned_agent_id=? ORDER BY updated_at DESC", (agent["id"],)))
            agent.update(agent_cost_summary(db, agent["id"]))
            agent["spent_usd"] = agent["monthly_actual_spend_usd"]
            agent["budgets"] = budget_rows(db, "agent", agent["id"])
        # Add executor availability for non-hermes agents
        BINARY_MAP = {"codex":"codex","claude-code":"claude","opencode":"opencode",
                      "agy":"agy","command-code":"cmd","kilo":"kilo"}
        for agent in agents:
            et = agent.get("executor_type", "hermes")
            if et != "hermes":
                binary = BINARY_MAP.get(et)
                agent["executor_available"] = bool(binary and shutil.which(binary))
            else:
                agent["executor_available"] = True  # native always available
    return templates.TemplateResponse(request=request, name="agents.html", context=template_context({"agents": agents}))


@app.get("/proposals/agents/{agent_id}", response_class=HTMLResponse)
@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, agent_id: str):
    with db_connect() as db:
        agent = row(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)))
        if not agent:
            return HTMLResponse("<h2>Not found</h2>", status_code=404)
        agent["tools"] = loads(agent.get("tools_allowed_json"), []) or []
        agent.update(agent_cost_summary(db, agent_id))
        if agent.get("executor_type") == "command-code":
            agent["node_version"] = get_node_version()
        agent["executor_spend_usd"] = float(db.execute(
            "SELECT COALESCE(SUM(actual_cost_usd),0) FROM usage_records WHERE scope_type='agent' AND scope_id=? AND executor_type!=''",
            (agent_id,)
        ).fetchone()[0] or 0)
        agent["spent_usd"] = agent["monthly_actual_spend_usd"]
        cards = enrich_proposals(db, "SELECT * FROM proposals WHERE assigned_agent_id=? ORDER BY updated_at DESC", (agent_id,))
        handoffs = rows(db.execute("SELECT h.*, fa.name AS from_agent, ta.name AS to_agent FROM agent_handoffs h LEFT JOIN agents fa ON fa.id=h.from_agent_id LEFT JOIN agents ta ON ta.id=h.to_agent_id WHERE h.from_agent_id=? OR h.to_agent_id=? ORDER BY h.created_at DESC", (agent_id, agent_id)))
        usage = rows(db.execute("SELECT * FROM usage_records WHERE scope_type='agent' AND scope_id=? ORDER BY created_at DESC", (agent_id,)))
        events = entity_events(db, "agent", agent_id)
        budgets = budget_rows(db, "agent", agent_id)
    return templates.TemplateResponse(request=request, name="agent_detail.html", context=template_context({"agent": agent, "cards": cards, "handoffs": handoffs, "usage": usage, "events": events, "budgets": budgets}))


@app.post("/api/proposals/agents")
@app.post("/api/agents")
async def create_agent(
    name: str = Form(...),
    role_title: str = Form(...),
    purpose: str = Form(""),
    system_prompt: str = Form(""),
    provider: str = Form("manual"),
    model_name: str = Form("manual"),
    executor_type: str = Form("hermes"),
    tools_allowed: str = Form(""),
    monthly_budget_usd: float = Form(0),
    manager_agent_id: str = Form(""),
    return_to: str = Form(""),
):
    if executor_type not in EXECUTOR_TYPES:
        return JSONResponse({"error": f"invalid executor_type: {executor_type}"}, status_code=400)
    agent_id = make_id("agent")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO agents
            (id,name,role_title,purpose,system_prompt,provider,model_name,executor_type,tools_allowed_json,
             monthly_budget_usd,manager_agent_id,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,NULLIF(?, ''),'active',?,?)
            """,
            (
                agent_id,
                name,
                role_title,
                purpose,
                system_prompt,
                provider,
                model_name,
                executor_type,
                lines_to_json(tools_allowed),
                monthly_budget_usd,
                manager_agent_id,
                now,
                now,
            ),
        )
        create_event(db, "human", "user", "agent", agent_id, "agent_created", {"name": name, "role_title": role_title})
        db.commit()
    return RedirectResponse(safe_return_path(return_to, "/proposals/agents"), status_code=303)


@app.post("/api/proposals/agents/from-template")
async def create_agent_from_template(
    template_key: str = Form(...),
    manager_agent_id: str = Form(""),
    return_to: str = Form(""),
):
    template = AGENT_TEMPLATE_DEFS.get(template_key)
    if not template:
        return JSONResponse({"error": "unknown agent template"}, status_code=404)
    agent_id = make_id("agent")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO agents
            (id,name,role_title,purpose,system_prompt,provider,model_name,executor_type,tools_allowed_json,
             monthly_budget_usd,manager_agent_id,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,NULLIF(?, ''),'active',?,?)
            """,
            (
                agent_id,
                template["name"],
                template["role_title"],
                template["purpose"],
                template["system_prompt"],
                template["provider"],
                template["model_name"],
                template.get("executor_type", "hermes"),
                dumps(template["tools_allowed"]),
                template["monthly_budget_usd"],
                manager_agent_id,
                now,
                now,
            ),
        )
        create_event(db, "human", "user", "agent", agent_id, "agent_created_from_template", {"template_key": template_key, "name": template["name"]})
        db.commit()
    default_return = f"/proposals/setup?view=org&agent_id={agent_id}"
    return RedirectResponse(safe_return_path(return_to, default_return), status_code=303)


@app.post("/api/proposals/agents/{agent_id}")
@app.post("/api/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    name: str = Form(...),
    role_title: str = Form(...),
    purpose: str = Form(""),
    system_prompt: str = Form(""),
    provider: str = Form("manual"),
    model_name: str = Form("manual"),
    executor_type: str = Form("hermes"),
    tools_allowed: str = Form(""),
    monthly_budget_usd: float = Form(0),
    manager_agent_id: str = Form(""),
    return_to: str = Form(""),
):
    if executor_type not in EXECUTOR_TYPES:
        return JSONResponse({"error": f"invalid executor_type: {executor_type}"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            UPDATE agents
            SET name=?, role_title=?, purpose=?, system_prompt=?, provider=?, model_name=?, executor_type=?,
                tools_allowed_json=?, monthly_budget_usd=?, manager_agent_id=NULLIF(?, ''), updated_at=?
            WHERE id=?
            """,
            (name, role_title, purpose, system_prompt, provider, model_name, executor_type, lines_to_json(tools_allowed), monthly_budget_usd, manager_agent_id, now, agent_id),
        )
        create_event(db, "human", "user", "agent", agent_id, "agent_updated", {"name": name, "role_title": role_title})
        db.commit()
    return RedirectResponse(safe_return_path(return_to, f"/proposals/agents/{agent_id}"), status_code=303)


@app.post("/api/proposals/agents/{agent_id}/status")
@app.post("/api/agents/{agent_id}/status")
async def update_agent_status(agent_id: str, status: str = Form(...), return_to: str = Form("")):
    if status not in AGENT_STATUSES:
        return JSONResponse({"error": "invalid status"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute("UPDATE agents SET status=?, updated_at=? WHERE id=?", (status, now, agent_id))
        create_event(db, "human", "user", "agent", agent_id, "agent_status_changed", {"status": status})
        db.commit()
    return RedirectResponse(safe_return_path(return_to, "/proposals/agents"), status_code=303)


@app.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request):
    with db_connect() as db:
        goals = rows(db.execute("SELECT * FROM goals ORDER BY updated_at DESC"))
        for goal in goals:
            goal["cards"] = enrich_proposals(db, "SELECT * FROM proposals WHERE goal_id=? ORDER BY updated_at DESC", (goal["id"],))
            goal["cost_total"] = scope_spend(db, "goal", goal["id"])
            goal["budgets"] = budget_rows(db, "goal", goal["id"])
    return templates.TemplateResponse(request=request, name="goals.html", context=template_context({"goals": goals}))


@app.get("/proposals/goals/{goal_id}", response_class=HTMLResponse)
@app.get("/goals/{goal_id}", response_class=HTMLResponse)
async def goal_detail(request: Request, goal_id: str):
    with db_connect() as db:
        goal = row(db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)))
        if not goal:
            return HTMLResponse("<h2>Not found</h2>", status_code=404)
        cards = enrich_proposals(db, "SELECT * FROM proposals WHERE goal_id=? ORDER BY updated_at DESC", (goal_id,))
        agents = rows(db.execute("SELECT DISTINCT a.* FROM agents a JOIN proposals p ON p.assigned_agent_id=a.id WHERE p.goal_id=? ORDER BY a.name", (goal_id,)))
        events = entity_events(db, "goal", goal_id)
        workflow_runs = rows(db.execute("SELECT wr.*, wt.name AS template_name FROM workflow_runs wr JOIN workflow_templates wt ON wt.id=wr.template_id WHERE wr.goal_id=? ORDER BY wr.updated_at DESC", (goal_id,)))
        budgets = budget_rows(db, "goal", goal_id)
        cost_total = scope_spend(db, "goal", goal_id)
    return templates.TemplateResponse(request=request, name="goal_detail.html", context=template_context({"goal": goal, "cards": cards, "agents": agents, "events": events, "workflow_runs": workflow_runs, "budgets": budgets, "cost_total": cost_total}))


@app.post("/api/proposals/goals")
@app.post("/api/goals")
async def create_goal(
    title: str = Form(...),
    desired_outcome: str = Form(""),
    success_metric: str = Form(""),
    priority: str = Form("medium"),
    owner_type: str = Form("human"),
    owner_id: str = Form("user"),
    due_date: str = Form(""),
    status: str = Form("planned"),
):
    if priority not in GOAL_PRIORITIES or status not in GOAL_STATUSES:
        return JSONResponse({"error": "invalid priority or status"}, status_code=400)
    goal_id = make_id("goal")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO goals
            (id,title,desired_outcome,success_metric,priority,owner_type,owner_id,due_date,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (goal_id, title, desired_outcome, success_metric, priority, owner_type, owner_id, due_date or None, status, now, now),
        )
        create_event(db, "human", "user", "goal", goal_id, "goal_created", {"title": title})
        db.commit()
    return RedirectResponse("/proposals/goals", status_code=303)


@app.post("/api/proposals/goals/{goal_id}")
@app.post("/api/goals/{goal_id}")
async def update_goal(
    goal_id: str,
    title: str = Form(...),
    desired_outcome: str = Form(""),
    success_metric: str = Form(""),
    priority: str = Form("medium"),
    owner_type: str = Form("human"),
    owner_id: str = Form("user"),
    due_date: str = Form(""),
    status: str = Form("planned"),
):
    if priority not in GOAL_PRIORITIES or status not in GOAL_STATUSES:
        return JSONResponse({"error": "invalid priority or status"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            UPDATE goals
            SET title=?, desired_outcome=?, success_metric=?, priority=?, owner_type=?, owner_id=?,
                due_date=NULLIF(?, ''), status=?, updated_at=?
            WHERE id=?
            """,
            (title, desired_outcome, success_metric, priority, owner_type, owner_id, due_date, status, now, goal_id),
        )
        create_event(db, "human", "user", "goal", goal_id, "goal_updated", {"title": title, "status": status})
        db.commit()
    return RedirectResponse(f"/proposals/goals/{goal_id}", status_code=303)


@app.get("/workflows", response_class=HTMLResponse)
async def workflows_page(request: Request):
    with db_connect() as db:
        templates_list = rows(db.execute("SELECT * FROM workflow_templates ORDER BY name"))
        for template in templates_list:
            template["stages"] = rows(db.execute("SELECT * FROM workflow_template_stages WHERE template_id=? ORDER BY position", (template["id"],)))
        runs = rows(db.execute("SELECT wr.*, wt.name AS template_name, p.title AS proposal_title, g.title AS goal_title FROM workflow_runs wr JOIN workflow_templates wt ON wt.id=wr.template_id LEFT JOIN proposals p ON p.id=wr.proposal_id LEFT JOIN goals g ON g.id=wr.goal_id ORDER BY wr.updated_at DESC"))
        proposals = rows(db.execute("SELECT id, title FROM proposals ORDER BY updated_at DESC LIMIT 100"))
        goals = rows(db.execute("SELECT id, title FROM goals ORDER BY updated_at DESC LIMIT 100"))
    return templates.TemplateResponse(request=request, name="workflows.html", context=template_context({"templates_list": templates_list, "runs": runs, "proposals": proposals, "goals": goals}))


@app.get("/proposals/workflows/runs/{run_id}", response_class=HTMLResponse)
@app.get("/workflows/runs/{run_id}", response_class=HTMLResponse)
async def workflow_run_detail(request: Request, run_id: str):
    with db_connect() as db:
        run = row(db.execute("SELECT wr.*, wt.name AS template_name, wt.description AS template_description, p.title AS proposal_title, g.title AS goal_title FROM workflow_runs wr JOIN workflow_templates wt ON wt.id=wr.template_id LEFT JOIN proposals p ON p.id=wr.proposal_id LEFT JOIN goals g ON g.id=wr.goal_id WHERE wr.id=?", (run_id,)))
        if not run:
            return HTMLResponse("<h2>Not found</h2>", status_code=404)
        stages = rows(
            db.execute(
                """
                SELECT s.*, aa.name AS assigned_agent_name, ha.name AS handoff_agent_name
                FROM workflow_run_stages s
                LEFT JOIN agents aa ON aa.id=s.assigned_agent_id
                LEFT JOIN agents ha ON ha.id=s.handoff_agent_id
                WHERE s.run_id=?
                ORDER BY s.position
                """,
                (run_id,),
            )
        )
        agents = rows(db.execute("SELECT * FROM agents ORDER BY name"))
        handoffs = rows(db.execute("SELECT h.*, fa.name AS from_agent, ta.name AS to_agent FROM agent_handoffs h LEFT JOIN agents fa ON fa.id=h.from_agent_id LEFT JOIN agents ta ON ta.id=h.to_agent_id WHERE h.workflow_run_id=? ORDER BY h.created_at DESC", (run_id,)))
        approvals = rows(db.execute("SELECT * FROM approval_requests WHERE entity_type='workflow_run' AND entity_id=? ORDER BY created_at DESC", (run_id,)))
        events = entity_events(db, "workflow_run", run_id)
    return templates.TemplateResponse(request=request, name="workflow_run_detail.html", context=template_context({"run": run, "stages": stages, "agents": agents, "handoffs": handoffs, "approvals": approvals, "events": events}))


@app.post("/api/proposals/workflow-templates")
async def create_workflow_template(name: str = Form(...), description: str = Form(""), return_to: str = Form("")):
    template_id = make_id("workflow")
    now = ts()
    with db_connect() as db:
        db.execute(
            "INSERT INTO workflow_templates (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (template_id, name, description, now, now),
        )
        create_event(db, "human", "user", "workflow_template", template_id, "workflow_template_created", {"name": name})
        db.commit()
    default_return = f"/proposals/setup?view=workflow&template_id={template_id}"
    return RedirectResponse(safe_return_path(return_to, default_return), status_code=303)


@app.post("/api/proposals/workflow-templates/{template_id}")
async def update_workflow_template(template_id: str, name: str = Form(...), description: str = Form(""), return_to: str = Form("")):
    now = ts()
    with db_connect() as db:
        db.execute(
            "UPDATE workflow_templates SET name=?, description=?, updated_at=? WHERE id=?",
            (name, description, now, template_id),
        )
        create_event(db, "human", "user", "workflow_template", template_id, "workflow_template_updated", {"name": name})
        db.commit()
    default_return = f"/proposals/setup?view=workflow&template_id={template_id}"
    return RedirectResponse(safe_return_path(return_to, default_return), status_code=303)


@app.post("/api/proposals/workflow-templates/{template_id}/stages")
async def create_workflow_template_stage(
    template_id: str,
    name: str = Form(...),
    description: str = Form(""),
    assigned_agent_id: str = Form(""),
    handoff_agent_id: str = Form(""),
    return_to: str = Form(""),
):
    stage_id = make_id("stage")
    now = ts()
    with db_connect() as db:
        template = row(db.execute("SELECT id FROM workflow_templates WHERE id=?", (template_id,)))
        if not template:
            return JSONResponse({"error": "unknown workflow template"}, status_code=404)
        position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 AS next_position FROM workflow_template_stages WHERE template_id=?", (template_id,)).fetchone()["next_position"]
        db.execute(
            """
            INSERT INTO workflow_template_stages
            (id, template_id, position, name, role_hint, description, assigned_agent_id, handoff_agent_id, created_at)
            VALUES (?, ?, ?, ?, '', ?, NULLIF(?, ''), NULLIF(?, ''), ?)
            """,
            (stage_id, template_id, position, name, description, assigned_agent_id, handoff_agent_id, now),
        )
        create_event(db, "human", "user", "workflow_template", template_id, "workflow_stage_created", {"stage_id": stage_id, "name": name})
        db.commit()
    default_return = f"/proposals/setup?view=workflow&template_id={template_id}&stage_id={stage_id}"
    return RedirectResponse(safe_return_path(return_to, default_return), status_code=303)


@app.post("/api/proposals/workflow-template-stages/{stage_id}")
async def update_workflow_template_stage(
    stage_id: str,
    name: str = Form(...),
    description: str = Form(""),
    position: int = Form(1),
    assigned_agent_id: str = Form(""),
    handoff_agent_id: str = Form(""),
    return_to: str = Form(""),
):
    with db_connect() as db:
        stage = row(db.execute("SELECT template_id FROM workflow_template_stages WHERE id=?", (stage_id,)))
        if not stage:
            return JSONResponse({"error": "unknown workflow template stage"}, status_code=404)
        template_id = stage["template_id"]
        db.execute(
            """
            UPDATE workflow_template_stages
            SET name=?, description=?, position=?, assigned_agent_id=NULLIF(?, ''), handoff_agent_id=NULLIF(?, '')
            WHERE id=?
            """,
            (name, description, max(1, position), assigned_agent_id, handoff_agent_id, stage_id),
        )
        normalize_stage_positions(db, template_id)
        create_event(db, "human", "user", "workflow_template", template_id, "workflow_stage_updated", {"stage_id": stage_id, "name": name})
        db.commit()
    default_return = f"/proposals/setup?view=workflow&template_id={template_id}&stage_id={stage_id}"
    return RedirectResponse(safe_return_path(return_to, default_return), status_code=303)


@app.post("/api/proposals/workflow-template-stages/{stage_id}/delete")
async def delete_workflow_template_stage(stage_id: str, return_to: str = Form("")):
    with db_connect() as db:
        stage = row(db.execute("SELECT template_id, name FROM workflow_template_stages WHERE id=?", (stage_id,)))
        if not stage:
            return JSONResponse({"error": "unknown workflow template stage"}, status_code=404)
        template_id = stage["template_id"]
        db.execute("DELETE FROM workflow_template_stages WHERE id=?", (stage_id,))
        normalize_stage_positions(db, template_id)
        create_event(db, "human", "user", "workflow_template", template_id, "workflow_stage_deleted", {"stage_id": stage_id, "name": stage["name"]})
        db.commit()
    default_return = f"/proposals/setup?view=workflow&template_id={template_id}"
    return RedirectResponse(safe_return_path(return_to, default_return), status_code=303)


@app.post("/api/proposals/workflows/start")
@app.post("/api/workflows/start")
async def start_workflow(template_id: str = Form(...), proposal_id: str = Form(""), goal_id: str = Form("")):
    run_id = make_id("run")
    now = ts()
    with db_connect() as db:
        stages = rows(db.execute("SELECT * FROM workflow_template_stages WHERE template_id=? ORDER BY position", (template_id,)))
        if not stages:
            return JSONResponse({"error": "unknown workflow template"}, status_code=404)
        first_stage_id = make_id("stage")
        db.execute(
            """
            INSERT INTO workflow_runs
            (id,template_id,proposal_id,goal_id,current_stage_id,status,started_by,created_at,updated_at)
            VALUES (?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, 'running', 'user', ?, ?)
            """,
            (run_id, template_id, proposal_id, goal_id, first_stage_id, now, now),
        )
        for index, stage in enumerate(stages):
            stage_id = first_stage_id if index == 0 else make_id("stage")
            db.execute(
                """
                INSERT INTO workflow_run_stages
                (id,run_id,template_stage_id,position,name,status,assigned_agent_id,handoff_agent_id,started_at,created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stage_id,
                    run_id,
                    stage["id"],
                    stage["position"],
                    stage["name"],
                    "current" if index == 0 else "pending",
                    stage.get("assigned_agent_id"),
                    stage.get("handoff_agent_id"),
                    now if index == 0 else None,
                    now,
                ),
            )
        create_event(db, "human", "user", "workflow_run", run_id, "workflow_started", {"template_id": template_id, "proposal_id": proposal_id or None, "goal_id": goal_id or None})
        if proposal_id:
            create_event(db, "human", "user", "proposal", proposal_id, "workflow_started", {"workflow_run_id": run_id})
        if goal_id:
            create_event(db, "human", "user", "goal", goal_id, "workflow_started", {"workflow_run_id": run_id})
        db.commit()
    return RedirectResponse(f"/proposals/workflows/runs/{run_id}", status_code=303)


@app.post("/api/proposals/workflows/runs/{run_id}/stages/{stage_id}")
@app.post("/api/workflows/runs/{run_id}/stages/{stage_id}")
async def update_workflow_stage(run_id: str, stage_id: str, status: str = Form(...), notes: str = Form("")):
    if status not in {"pending", "current", "completed", "failed", "skipped"}:
        return JSONResponse({"error": "invalid stage status"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute(
            "UPDATE workflow_run_stages SET status=?, notes=?, completed_at=CASE WHEN ? IN ('completed','failed','skipped') THEN ? ELSE completed_at END WHERE id=? AND run_id=?",
            (status, notes, status, now, stage_id, run_id),
        )
        if status == "completed":
            next_stage = db.execute(
                "SELECT id FROM workflow_run_stages WHERE run_id=? AND status='pending' ORDER BY position LIMIT 1",
                (run_id,),
            ).fetchone()
            if next_stage:
                db.execute("UPDATE workflow_run_stages SET status='current', started_at=? WHERE id=?", (now, next_stage["id"]))
                db.execute("UPDATE workflow_runs SET current_stage_id=?, updated_at=? WHERE id=?", (next_stage["id"], now, run_id))
        create_event(db, "human", "user", "workflow_run", run_id, "workflow_stage_updated", {"stage_id": stage_id, "status": status})
        db.commit()
    return RedirectResponse(f"/proposals/workflows/runs/{run_id}", status_code=303)


@app.post("/api/proposals/workflows/runs/{run_id}/status")
@app.post("/api/workflows/runs/{run_id}/status")
async def update_workflow_status(run_id: str, status: str = Form(...)):
    if status not in WORKFLOW_RUN_STATUSES:
        return JSONResponse({"error": "invalid workflow status"}, status_code=400)
    now = ts()
    with db_connect() as db:
        failed_count = db.execute("SELECT COUNT(*) AS total FROM workflow_run_stages WHERE run_id=? AND status='failed'", (run_id,)).fetchone()["total"]
        if status == "completed" and failed_count:
            create_approval(
                db,
                "workflow_run",
                run_id,
                "Complete workflow with failed stages",
                "high",
                "Default policy requires approval before completing a workflow that has failed stages.",
                payload={"failed_stage_count": failed_count},
            )
        db.execute(
            "UPDATE workflow_runs SET status=?, updated_at=?, completed_at=CASE WHEN ? IN ('completed','failed') THEN ? ELSE completed_at END WHERE id=?",
            (status, now, status, now, run_id),
        )
        create_event(db, "human", "user", "workflow_run", run_id, "workflow_status_changed", {"status": status})
        db.commit()
    return RedirectResponse(f"/proposals/workflows/runs/{run_id}", status_code=303)


@app.post("/api/proposals/workflows/runs/{run_id}/handoffs")
@app.post("/api/workflows/runs/{run_id}/handoffs")
async def create_handoff(
    run_id: str,
    from_agent_id: str = Form(""),
    to_agent_id: str = Form(""),
    proposal_id: str = Form(""),
    goal_id: str = Form(""),
    reason: str = Form(""),
    context_summary: str = Form(""),
):
    handoff_id = make_id("handoff")
    now = ts()
    with db_connect() as db:
        run = row(db.execute("SELECT proposal_id, goal_id FROM workflow_runs WHERE id=?", (run_id,)))
        db.execute(
            """
            INSERT INTO agent_handoffs
            (id,from_agent_id,to_agent_id,proposal_id,goal_id,workflow_run_id,reason,context_summary,status,created_at,updated_at)
            VALUES (?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?, 'requested', ?, ?)
            """,
            (handoff_id, from_agent_id, to_agent_id, proposal_id or (run or {}).get("proposal_id") or "", goal_id or (run or {}).get("goal_id") or "", run_id, reason, context_summary, now, now),
        )
        create_event(db, "human", "user", "workflow_run", run_id, "handoff_requested", {"handoff_id": handoff_id, "from_agent_id": from_agent_id, "to_agent_id": to_agent_id})
        db.commit()
    return RedirectResponse(f"/proposals/workflows/runs/{run_id}", status_code=303)


@app.post("/api/handoffs/{handoff_id}/status")
async def update_handoff_status(handoff_id: str, status: str = Form(...)):
    if status not in HANDOFF_STATUSES:
        return JSONResponse({"error": "invalid handoff status"}, status_code=400)
    now = ts()
    with db_connect() as db:
        handoff = row(db.execute("SELECT * FROM agent_handoffs WHERE id=?", (handoff_id,)))
        if not handoff:
            return JSONResponse({"error": "not found"}, status_code=404)
        db.execute("UPDATE agent_handoffs SET status=?, updated_at=? WHERE id=?", (status, now, handoff_id))
        if handoff.get("workflow_run_id"):
            create_event(db, "human", "user", "workflow_run", handoff["workflow_run_id"], "handoff_status_changed", {"handoff_id": handoff_id, "status": status})
        db.commit()
    target = f"/proposals/workflows/runs/{handoff['workflow_run_id']}" if handoff.get("workflow_run_id") else "/proposals/workflows"
    return RedirectResponse(target, status_code=303)


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request):
    with db_connect() as db:
        approvals = rows(db.execute("SELECT * FROM approval_requests ORDER BY CASE risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC"))
        policies = rows(db.execute("SELECT * FROM approval_policies ORDER BY name"))
    grouped = {level: [a for a in approvals if a["risk_level"] == level] for level in ["critical", "high", "medium", "low"]}
    return templates.TemplateResponse(request=request, name="approvals.html", context=template_context({"approvals": approvals, "grouped": grouped, "policies": policies}))


@app.post("/api/proposals/approvals/{approval_id}/decision")
@app.post("/api/approvals/{approval_id}/decision")
async def decide_approval(approval_id: str, status: str = Form(...), decision_reason: str = Form("")):
    if status not in {"approved", "rejected"}:
        return JSONResponse({"error": "invalid approval decision"}, status_code=400)
    now = ts()
    with db_connect() as db:
        approval = row(db.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)))
        if not approval:
            return JSONResponse({"error": "not found"}, status_code=404)
        db.execute(
            "UPDATE approval_requests SET status=?, decision_reason=?, decided_by='user', decided_at=?, updated_at=? WHERE id=?",
            (status, decision_reason, now, now, approval_id),
        )
        create_event(db, "human", "user", approval["entity_type"], approval["entity_id"], f"approval_{status}", {"approval_id": approval_id, "decision_reason": decision_reason})
        db.commit()
    return RedirectResponse("/proposals/approvals", status_code=303)


@app.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request):
    with db_connect() as db:
        budgets = budget_rows(db)
        agents = rows(db.execute("SELECT id, name FROM agents ORDER BY name"))
        goals = rows(db.execute("SELECT id, title FROM goals ORDER BY title"))
        workflows = rows(db.execute("SELECT id FROM workflow_runs ORDER BY updated_at DESC"))
        projects = rows(db.execute("SELECT DISTINCT board AS id FROM proposals ORDER BY board"))
    return templates.TemplateResponse(request=request, name="budgets.html", context=template_context({"budgets": budgets, "agents": agents, "goals": goals, "workflows": workflows, "projects": projects}))


@app.post("/api/proposals/budgets")
@app.post("/api/budgets")
async def create_budget(
    scope_type: str = Form(...),
    scope_id: str = Form(...),
    period: str = Form("monthly"),
    limit_usd: float = Form(...),
    behavior_on_limit: str = Form("warn"),
):
    budget_id = make_id("budget")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO budgets
            (id,scope_type,scope_id,period,limit_usd,behavior_on_limit,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (budget_id, scope_type, scope_id, period, limit_usd, behavior_on_limit, now, now),
        )
        create_event(db, "human", "user", "budget", budget_id, "budget_created", {"scope_type": scope_type, "scope_id": scope_id, "limit_usd": limit_usd})
        db.commit()
    return RedirectResponse("/proposals/budgets", status_code=303)


@app.post("/api/proposals/budgets/{budget_id}")
@app.post("/api/budgets/{budget_id}")
async def update_budget(
    budget_id: str,
    period: str = Form("monthly"),
    limit_usd: float = Form(...),
    behavior_on_limit: str = Form("warn"),
):
    now = ts()
    with db_connect() as db:
        db.execute(
            "UPDATE budgets SET period=?, limit_usd=?, behavior_on_limit=?, updated_at=? WHERE id=?",
            (period, limit_usd, behavior_on_limit, now, budget_id),
        )
        create_event(db, "human", "user", "budget", budget_id, "budget_updated", {"limit_usd": limit_usd, "behavior_on_limit": behavior_on_limit})
        db.commit()
    return RedirectResponse("/proposals/budgets", status_code=303)


@app.post("/api/proposals/usage")
@app.post("/api/usage")
async def create_usage_record(
    scope_type: str = Form(...),
    scope_id: str = Form(...),
    provider: str = Form("manual"),
    model: str = Form("manual"),
    input_tokens: int = Form(0),
    output_tokens: int = Form(0),
    cached_tokens: int = Form(0),
    tool_call_count: int = Form(0),
    estimated_cost_usd: float = Form(0),
    actual_cost_usd: float = Form(0),
    manual_note: str = Form(""),
    executor_type: str = Form(""),
):
    usage_id = make_id("usage")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO usage_records
            (id,scope_type,scope_id,provider,model,input_tokens,output_tokens,cached_tokens,
             tool_call_count,estimated_cost_usd,actual_cost_usd,manual_note,executor_type,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (usage_id, scope_type, scope_id, provider, model, input_tokens, output_tokens, cached_tokens, tool_call_count, estimated_cost_usd, actual_cost_usd, manual_note, executor_type, now),
        )
        create_event(db, "human", "user", scope_type, scope_id, "usage_recorded", {"usage_id": usage_id, "estimated_cost_usd": estimated_cost_usd, "actual_cost_usd": actual_cost_usd})
        if scope_type == "proposal":
            ensure_policy_approvals(db, scope_id)
        db.commit()
    referer = "/proposals/budgets"
    if scope_type == "agent":
        referer = f"/proposals/agents/{scope_id}"
    elif scope_type == "proposal":
        referer = f"/proposals/{scope_id}"
    elif scope_type == "goal":
        referer = f"/proposals/goals/{scope_id}"
    elif scope_type == "workflow":
        referer = f"/proposals/workflows/runs/{scope_id}"
    return RedirectResponse(referer, status_code=303)
