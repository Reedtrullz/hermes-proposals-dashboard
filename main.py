import json
import os
import sqlite3
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

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ts() -> int:
    return int(time.time())


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


def get_profiles() -> list[str]:
    if PROFILES_DIR.is_dir():
        return sorted(d.name for d in PROFILES_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists())
    return []


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
            tools_allowed_json TEXT NOT NULL DEFAULT '[]',
            monthly_budget_usd REAL NOT NULL DEFAULT 0,
            manager_agent_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
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
            created_at INTEGER NOT NULL
        )
        """
    )
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
            started_at INTEGER,
            completed_at INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    ensure_column(db, "workflow_run_stages", "created_at", "INTEGER NOT NULL DEFAULT 0")
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
            manual_note TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        )
        """
    )
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
        ("agent_product_lead", "Product Lead", "Product Strategist", "Turns goals into epics and acceptance criteria.", "Clarify outcomes, create structured backlog, and avoid code changes.", "openai", "gpt-4.1", ["comment", "create_goal", "create_card"], 25, None),
        ("agent_architect", "Architect", "Technical Architect", "Breaks goals into implementation plans and risks.", "Produce pragmatic plans, dependencies, and risks.", "openai", "gpt-4.1", ["comment", "create_subtask", "handoff"], 25, "agent_product_lead"),
        ("agent_builder", "Builder", "Implementation Agent", "Implements approved tasks and reports changes.", "Work in small reviewed changes and ask for approval before risky operations.", "openai", "gpt-4.1", ["comment", "propose_patch"], 50, "agent_architect"),
        ("agent_reviewer", "Reviewer", "Code Reviewer", "Reviews output for quality, risks, and acceptance criteria.", "Prioritize bugs, regressions, and missing tests.", "openai", "gpt-4.1", ["comment", "request_changes", "handoff"], 20, "agent_architect"),
        ("agent_qa", "QA Agent", "Quality Analyst", "Creates test plans and validates acceptance criteria.", "Turn acceptance criteria into focused test scenarios.", "openai", "gpt-4.1", ["comment", "create_test_plan"], 15, "agent_reviewer"),
        ("agent_cost", "Cost Controller", "Cost Controller", "Watches spend and flags runaway work.", "Warn on cost overruns and pause expensive loops.", "manual", "manual", ["comment", "request_approval"], 10, None),
    ]
    for agent in agents:
        db.execute(
            """
            INSERT OR IGNORE INTO agents
            (id, name, role_title, purpose, system_prompt, provider, model_name, tools_allowed_json,
             monthly_budget_usd, manager_agent_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (*agent[:7], dumps(agent[7]), agent[8], agent[9], now, now),
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
        p_total = db.execute("SELECT COALESCE(SUM(estimated_cost_usd + actual_cost_usd), 0) AS total FROM proposals").fetchone()["total"]
        u_total = db.execute("SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM usage_records").fetchone()["total"]
        return float(p_total or 0) + float(u_total or 0)
    if scope_type == "goal":
        p_total = db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd + actual_cost_usd), 0) AS total FROM proposals WHERE goal_id=?",
            (scope_id,),
        ).fetchone()["total"]
        return float(p_total or 0) + usage_total(db, scope_type, scope_id)
    if scope_type == "agent":
        p_total = db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd + actual_cost_usd), 0) AS total FROM proposals WHERE assigned_agent_id=?",
            (scope_id,),
        ).fetchone()["total"]
        return float(p_total or 0) + usage_total(db, scope_type, scope_id)
    if scope_type == "project":
        p_total = db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd + actual_cost_usd), 0) AS total FROM proposals WHERE board=?",
            (scope_id,),
        ).fetchone()["total"]
        return float(p_total or 0) + usage_total(db, scope_type, scope_id)
    return usage_total(db, scope_type, scope_id)


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
    with db_connect() as db:
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
            context=template_context({"proposals": proposals, "profiles": get_profiles()}),
        )


@app.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def proposal_detail(request: Request, proposal_id: str):
    with db_connect() as db:
        context = proposal_context(db, proposal_id)
    if not context:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    return templates.TemplateResponse(request=request, name="proposal_detail.html", context=template_context({**context, "profiles": get_profiles()}))


@app.get("/api/proposals/{proposal_id}/fragment", response_class=HTMLResponse)
async def proposal_fragment(request: Request, proposal_id: str):
    with db_connect() as db:
        context = proposal_context(db, proposal_id)
    if not context:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request=request, name="_proposal_detail_fragment.html", context=template_context(context))


@app.post("/api/proposals")
async def create_proposal(title: str = Form(...), body: str = Form(""), board: str = Form("default")):
    pid = make_id("p")
    now = ts()
    with db_connect() as db:
        db.execute(
            "INSERT INTO proposals (id,title,body,status,board,created_at,updated_at) VALUES (?,?,?,'processing',?,?,?)",
            (pid, title, body, board, now, now),
        )
        create_event(db, "human", "user", "proposal", pid, "proposal_created", {"title": title, "board": board})
        db.commit()
    TRIGGER_FILE.write_text(pid)
    return {"ok": True, "id": pid}


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
            agent["spent_usd"] = scope_spend(db, "agent", agent["id"])
            agent["budgets"] = budget_rows(db, "agent", agent["id"])
    return templates.TemplateResponse(request=request, name="agents.html", context=template_context({"agents": agents}))


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, agent_id: str):
    with db_connect() as db:
        agent = row(db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)))
        if not agent:
            return HTMLResponse("<h2>Not found</h2>", status_code=404)
        agent["tools"] = loads(agent.get("tools_allowed_json"), []) or []
        agent["spent_usd"] = scope_spend(db, "agent", agent_id)
        cards = enrich_proposals(db, "SELECT * FROM proposals WHERE assigned_agent_id=? ORDER BY updated_at DESC", (agent_id,))
        handoffs = rows(db.execute("SELECT h.*, fa.name AS from_agent, ta.name AS to_agent FROM agent_handoffs h LEFT JOIN agents fa ON fa.id=h.from_agent_id LEFT JOIN agents ta ON ta.id=h.to_agent_id WHERE h.from_agent_id=? OR h.to_agent_id=? ORDER BY h.created_at DESC", (agent_id, agent_id)))
        usage = rows(db.execute("SELECT * FROM usage_records WHERE scope_type='agent' AND scope_id=? ORDER BY created_at DESC", (agent_id,)))
        events = entity_events(db, "agent", agent_id)
        budgets = budget_rows(db, "agent", agent_id)
    return templates.TemplateResponse(request=request, name="agent_detail.html", context=template_context({"agent": agent, "cards": cards, "handoffs": handoffs, "usage": usage, "events": events, "budgets": budgets}))


@app.post("/api/agents")
async def create_agent(
    name: str = Form(...),
    role_title: str = Form(...),
    purpose: str = Form(""),
    system_prompt: str = Form(""),
    provider: str = Form("manual"),
    model_name: str = Form("manual"),
    tools_allowed: str = Form(""),
    monthly_budget_usd: float = Form(0),
    manager_agent_id: str = Form(""),
):
    agent_id = make_id("agent")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO agents
            (id,name,role_title,purpose,system_prompt,provider,model_name,tools_allowed_json,
             monthly_budget_usd,manager_agent_id,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,NULLIF(?, ''),'active',?,?)
            """,
            (
                agent_id,
                name,
                role_title,
                purpose,
                system_prompt,
                provider,
                model_name,
                lines_to_json(tools_allowed),
                monthly_budget_usd,
                manager_agent_id,
                now,
                now,
            ),
        )
        create_event(db, "human", "user", "agent", agent_id, "agent_created", {"name": name, "role_title": role_title})
        db.commit()
    return RedirectResponse("/agents", status_code=303)


@app.post("/api/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    name: str = Form(...),
    role_title: str = Form(...),
    purpose: str = Form(""),
    system_prompt: str = Form(""),
    provider: str = Form("manual"),
    model_name: str = Form("manual"),
    tools_allowed: str = Form(""),
    monthly_budget_usd: float = Form(0),
    manager_agent_id: str = Form(""),
):
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            UPDATE agents
            SET name=?, role_title=?, purpose=?, system_prompt=?, provider=?, model_name=?,
                tools_allowed_json=?, monthly_budget_usd=?, manager_agent_id=NULLIF(?, ''), updated_at=?
            WHERE id=?
            """,
            (name, role_title, purpose, system_prompt, provider, model_name, lines_to_json(tools_allowed), monthly_budget_usd, manager_agent_id, now, agent_id),
        )
        create_event(db, "human", "user", "agent", agent_id, "agent_updated", {"name": name, "role_title": role_title})
        db.commit()
    return RedirectResponse(f"/agents/{agent_id}", status_code=303)


@app.post("/api/agents/{agent_id}/status")
async def update_agent_status(agent_id: str, status: str = Form(...)):
    if status not in AGENT_STATUSES:
        return JSONResponse({"error": "invalid status"}, status_code=400)
    now = ts()
    with db_connect() as db:
        db.execute("UPDATE agents SET status=?, updated_at=? WHERE id=?", (status, now, agent_id))
        create_event(db, "human", "user", "agent", agent_id, "agent_status_changed", {"status": status})
        db.commit()
    return RedirectResponse("/agents", status_code=303)


@app.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request):
    with db_connect() as db:
        goals = rows(db.execute("SELECT * FROM goals ORDER BY updated_at DESC"))
        for goal in goals:
            goal["cards"] = enrich_proposals(db, "SELECT * FROM proposals WHERE goal_id=? ORDER BY updated_at DESC", (goal["id"],))
            goal["cost_total"] = scope_spend(db, "goal", goal["id"])
            goal["budgets"] = budget_rows(db, "goal", goal["id"])
    return templates.TemplateResponse(request=request, name="goals.html", context=template_context({"goals": goals}))


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
    return RedirectResponse("/goals", status_code=303)


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
    return RedirectResponse(f"/goals/{goal_id}", status_code=303)


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


@app.get("/workflows/runs/{run_id}", response_class=HTMLResponse)
async def workflow_run_detail(request: Request, run_id: str):
    with db_connect() as db:
        run = row(db.execute("SELECT wr.*, wt.name AS template_name, wt.description AS template_description, p.title AS proposal_title, g.title AS goal_title FROM workflow_runs wr JOIN workflow_templates wt ON wt.id=wr.template_id LEFT JOIN proposals p ON p.id=wr.proposal_id LEFT JOIN goals g ON g.id=wr.goal_id WHERE wr.id=?", (run_id,)))
        if not run:
            return HTMLResponse("<h2>Not found</h2>", status_code=404)
        stages = rows(db.execute("SELECT * FROM workflow_run_stages WHERE run_id=? ORDER BY position", (run_id,)))
        agents = rows(db.execute("SELECT * FROM agents ORDER BY name"))
        handoffs = rows(db.execute("SELECT h.*, fa.name AS from_agent, ta.name AS to_agent FROM agent_handoffs h LEFT JOIN agents fa ON fa.id=h.from_agent_id LEFT JOIN agents ta ON ta.id=h.to_agent_id WHERE h.workflow_run_id=? ORDER BY h.created_at DESC", (run_id,)))
        approvals = rows(db.execute("SELECT * FROM approval_requests WHERE entity_type='workflow_run' AND entity_id=? ORDER BY created_at DESC", (run_id,)))
        events = entity_events(db, "workflow_run", run_id)
    return templates.TemplateResponse(request=request, name="workflow_run_detail.html", context=template_context({"run": run, "stages": stages, "agents": agents, "handoffs": handoffs, "approvals": approvals, "events": events}))


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
                (id,run_id,template_stage_id,position,name,status,started_at,created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (stage_id, run_id, stage["id"], stage["position"], stage["name"], "current" if index == 0 else "pending", now if index == 0 else None, now),
            )
        create_event(db, "human", "user", "workflow_run", run_id, "workflow_started", {"template_id": template_id, "proposal_id": proposal_id or None, "goal_id": goal_id or None})
        if proposal_id:
            create_event(db, "human", "user", "proposal", proposal_id, "workflow_started", {"workflow_run_id": run_id})
        if goal_id:
            create_event(db, "human", "user", "goal", goal_id, "workflow_started", {"workflow_run_id": run_id})
        db.commit()
    return RedirectResponse(f"/workflows/runs/{run_id}", status_code=303)


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
    return RedirectResponse(f"/workflows/runs/{run_id}", status_code=303)


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
    return RedirectResponse(f"/workflows/runs/{run_id}", status_code=303)


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
    return RedirectResponse(f"/workflows/runs/{run_id}", status_code=303)


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
    target = f"/workflows/runs/{handoff['workflow_run_id']}" if handoff.get("workflow_run_id") else "/workflows"
    return RedirectResponse(target, status_code=303)


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request):
    with db_connect() as db:
        approvals = rows(db.execute("SELECT * FROM approval_requests ORDER BY CASE risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC"))
        policies = rows(db.execute("SELECT * FROM approval_policies ORDER BY name"))
    grouped = {level: [a for a in approvals if a["risk_level"] == level] for level in ["critical", "high", "medium", "low"]}
    return templates.TemplateResponse(request=request, name="approvals.html", context=template_context({"approvals": approvals, "grouped": grouped, "policies": policies}))


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
    return RedirectResponse("/approvals", status_code=303)


@app.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request):
    with db_connect() as db:
        budgets = budget_rows(db)
        agents = rows(db.execute("SELECT id, name FROM agents ORDER BY name"))
        goals = rows(db.execute("SELECT id, title FROM goals ORDER BY title"))
        workflows = rows(db.execute("SELECT id FROM workflow_runs ORDER BY updated_at DESC"))
        projects = rows(db.execute("SELECT DISTINCT board AS id FROM proposals ORDER BY board"))
    return templates.TemplateResponse(request=request, name="budgets.html", context=template_context({"budgets": budgets, "agents": agents, "goals": goals, "workflows": workflows, "projects": projects}))


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
    return RedirectResponse("/budgets", status_code=303)


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
    return RedirectResponse("/budgets", status_code=303)


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
    manual_note: str = Form(""),
):
    usage_id = make_id("usage")
    now = ts()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO usage_records
            (id,scope_type,scope_id,provider,model,input_tokens,output_tokens,cached_tokens,
             tool_call_count,estimated_cost_usd,manual_note,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (usage_id, scope_type, scope_id, provider, model, input_tokens, output_tokens, cached_tokens, tool_call_count, estimated_cost_usd, manual_note, now),
        )
        create_event(db, "human", "user", scope_type, scope_id, "usage_recorded", {"usage_id": usage_id, "estimated_cost_usd": estimated_cost_usd})
        if scope_type == "proposal":
            ensure_policy_approvals(db, scope_id)
        db.commit()
    referer = "/budgets"
    return RedirectResponse(referer, status_code=303)
