import importlib
import os
import sqlite3
import sys

from fastapi.testclient import TestClient


def load_main(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_REQUIRE_AUTH", "0")
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_migrates_existing_proposals_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "proposals.db"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE proposals (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft', author TEXT NOT NULL DEFAULT 'user', board TEXT NOT NULL DEFAULT 'default', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE proposal_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    db.commit()
    db.close()

    main = load_main(tmp_path, monkeypatch)
    with main.db_connect() as db:
        proposal_cols = {r["name"] for r in db.execute("PRAGMA table_info(proposals)").fetchall()}
        template_stage_cols = {r["name"] for r in db.execute("PRAGMA table_info(workflow_template_stages)").fetchall()}
        run_stage_cols = {r["name"] for r in db.execute("PRAGMA table_info(workflow_run_stages)").fetchall()}
        usage_cols = {r["name"] for r in db.execute("PRAGMA table_info(usage_records)").fetchall()}
        assert {"goal_id", "parent_id", "assigned_agent_id", "acceptance_criteria_json", "risk_level", "estimated_cost_usd", "actual_cost_usd"} <= proposal_cols
        assert {"assigned_agent_id", "handoff_agent_id"} <= template_stage_cols
        assert {"assigned_agent_id", "handoff_agent_id"} <= run_stage_cols
        assert "actual_cost_usd" in usage_cols
        assert db.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"] >= 6
        assert db.execute("SELECT COUNT(*) AS n FROM workflow_templates").fetchone()["n"] == 3
        assert db.execute("SELECT COUNT(*) AS n FROM approval_policies").fetchone()["n"] == 3


def test_health_endpoint_bypasses_auth(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "ok"}


def test_existing_card_api_and_agent_goal_linking(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    created = client.post("/api/proposals", data={"title": "Build agent ops", "body": "Keep kanban central", "board": "default"}).json()
    proposal_id = created["id"]
    assert (tmp_path / "proposals_trigger").read_text() == proposal_id

    client.post(
        "/api/goals",
        data={
            "title": "Launch supervised agents",
            "desired_outcome": "Users can supervise agent work",
            "success_metric": "All foundations visible",
            "priority": "high",
            "status": "active",
        },
    )
    with main.db_connect() as db:
        goal_id = db.execute("SELECT id FROM goals WHERE title='Launch supervised agents'").fetchone()["id"]
        agent_id = db.execute("SELECT id FROM agents WHERE id='agent_builder'").fetchone()["id"]

    response = client.post(
        f"/api/proposals/{proposal_id}/metadata",
        data={
            "goal_id": goal_id,
            "assigned_agent_id": agent_id,
            "risk_level": "critical",
            "acceptance_criteria": "Goal breadcrumb is visible\nAgent is assigned",
            "estimated_cost_usd": "3.50",
            "actual_cost_usd": "0",
        },
    )
    assert response.status_code == 200

    detail = client.get(f"/api/proposals/{proposal_id}").json()
    assert detail["goal_id"] == goal_id
    assert detail["assigned_agent_id"] == agent_id
    assert detail["risk_level"] == "critical"
    assert detail["criteria"] == ["Goal breadcrumb is visible", "Agent is assigned"]
    assert detail["cost_total"] == 3.5

    with main.db_connect() as db:
        approvals = db.execute("SELECT title FROM approval_requests WHERE entity_type='proposal' AND entity_id=?", (proposal_id,)).fetchall()
        titles = {r["title"] for r in approvals}
        assert "Critical-risk card requires approval" in titles
        assert "Cost threshold exceeded" in titles
        events = db.execute("SELECT event_type FROM audit_events WHERE entity_type='proposal' AND entity_id=?", (proposal_id,)).fetchall()
        assert "proposal_metadata_updated" in {r["event_type"] for r in events}


def test_cards_page_renders_cost_helpers(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    client.post("/api/proposals", data={"title": "Rendered card"})

    response = client.get("/proposals")

    assert response.status_code == 200
    assert "Rendered card" in response.text
    assert "$0.00" in response.text
    assert 'href="/proposals/goals"' in response.text
    assert 'href="/proposals/agents"' in response.text
    assert 'href="/proposals/workflows"' in response.text
    assert 'href="/proposals/approvals"' in response.text
    assert 'href="/proposals/budgets"' in response.text
    assert 'href="/proposals/setup"' in response.text


def test_prefixed_ops_pages_and_api_routes_work_under_proposals(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    for path in ["/proposals/setup", "/proposals/goals", "/proposals/agents", "/proposals/workflows", "/proposals/approvals", "/proposals/budgets"]:
        response = client.get(path)
        assert response.status_code == 200, path

    goal_response = client.post(
        "/api/proposals/goals",
        data={"title": "Prefixed goal", "priority": "medium", "status": "active"},
        follow_redirects=False,
    )

    assert goal_response.status_code == 303
    assert goal_response.headers["location"] == "/proposals/goals"


def test_setup_page_uses_prefixed_links_and_actions(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.get("/proposals/setup?view=org&agent_id=agent_builder")

    assert response.status_code == 200
    assert 'href="/proposals/setup?view=org' in response.text
    assert 'href="/proposals/setup?view=workflow' in response.text
    assert 'action="/api/proposals/agents/from-template"' in response.text
    assert 'action="/api/proposals/workflow-templates"' in response.text
    assert 'data-node="agent_builder"' in response.text
    assert 'data-manager="agent_architect"' in response.text


def test_org_setup_create_agent_from_template_and_update_manager(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    created = client.post(
        "/api/proposals/agents/from-template",
        data={"template_key": "builder", "manager_agent_id": "agent_architect"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"].startswith("/proposals/setup?view=org&agent_id=")
    agent_id = created.headers["location"].split("agent_id=", 1)[1]

    with main.db_connect() as db:
        agent = db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        assert agent["manager_agent_id"] == "agent_architect"

    updated = client.post(
        f"/api/proposals/agents/{agent_id}",
        data={
            "name": "Codex Builder",
            "role_title": "Software Engineer",
            "purpose": "Build approved tasks",
            "system_prompt": "Implement scoped work.",
            "provider": "openai",
            "model_name": "gpt-4.1",
            "tools_allowed": "comment\npropose_patch",
            "monthly_budget_usd": "30",
            "manager_agent_id": "agent_product_lead",
            "return_to": f"/proposals/setup?view=org&agent_id={agent_id}",
        },
        follow_redirects=False,
    )
    assert updated.headers["location"] == f"/proposals/setup?view=org&agent_id={agent_id}"

    page = client.get(f"/proposals/setup?view=org&agent_id={agent_id}")
    assert page.status_code == 200
    assert "Codex Builder" in page.text
    assert f'data-node="{agent_id}" data-manager="agent_product_lead"' in page.text


def test_workflow_template_setup_crud_and_stage_agent_handoff(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    created = client.post(
        "/api/proposals/workflow-templates",
        data={"name": "Release Review", "description": "Coordinate release readiness"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"].startswith("/proposals/setup?view=workflow&template_id=")

    with main.db_connect() as db:
        template_id = db.execute("SELECT id FROM workflow_templates WHERE name='Release Review'").fetchone()["id"]

    renamed = client.post(
        f"/api/proposals/workflow-templates/{template_id}",
        data={"name": "Release Gate", "description": "Check release gates"},
        follow_redirects=False,
    )
    assert renamed.status_code == 303

    stage_response = client.post(
        f"/api/proposals/workflow-templates/{template_id}/stages",
        data={
            "name": "Implementation",
            "description": "Build the scoped change",
            "assigned_agent_id": "agent_builder",
            "handoff_agent_id": "agent_reviewer",
        },
        follow_redirects=False,
    )
    assert stage_response.status_code == 303

    with main.db_connect() as db:
        stage = db.execute("SELECT * FROM workflow_template_stages WHERE template_id=?", (template_id,)).fetchone()
        assert stage["assigned_agent_id"] == "agent_builder"
        assert stage["handoff_agent_id"] == "agent_reviewer"
        stage_id = stage["id"]

    update_stage = client.post(
        f"/api/proposals/workflow-template-stages/{stage_id}",
        data={
            "name": "Reviewed Implementation",
            "description": "Build and prepare for QA",
            "position": "1",
            "assigned_agent_id": "agent_architect",
            "handoff_agent_id": "agent_builder",
        },
        follow_redirects=False,
    )
    assert update_stage.status_code == 303

    with main.db_connect() as db:
        stage = db.execute("SELECT * FROM workflow_template_stages WHERE id=?", (stage_id,)).fetchone()
        assert stage["name"] == "Reviewed Implementation"
        assert stage["assigned_agent_id"] == "agent_architect"
        assert stage["handoff_agent_id"] == "agent_builder"

    delete_stage = client.post(f"/api/proposals/workflow-template-stages/{stage_id}/delete", follow_redirects=False)
    assert delete_stage.status_code == 303
    with main.db_connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM workflow_template_stages WHERE id=?", (stage_id,)).fetchone()["n"] == 0


def test_agent_crud_pause_resume_and_budget_meter_data(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    client.post(
        "/api/agents",
        data={
            "name": "Security Reviewer",
            "role_title": "Security Reviewer",
            "purpose": "Review risky work",
            "system_prompt": "Find security issues.",
            "provider": "manual",
            "model_name": "manual",
            "tools_allowed": "comment\nrequest_approval",
            "monthly_budget_usd": "12",
        },
    )
    with main.db_connect() as db:
        agent = db.execute("SELECT * FROM agents WHERE name='Security Reviewer'").fetchone()
        assert agent["status"] == "active"
        assert main.loads(agent["tools_allowed_json"], []) == ["comment", "request_approval"]

    client.post(f"/api/agents/{agent['id']}/status", data={"status": "paused"})
    with main.db_connect() as db:
        assert db.execute("SELECT status FROM agents WHERE id=?", (agent["id"],)).fetchone()["status"] == "paused"


def test_agent_actual_usage_records_drive_monthly_budget(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    agent_id = "agent_builder"

    response = client.post(
        "/api/proposals/usage",
        data={
            "scope_type": "agent",
            "scope_id": agent_id,
            "provider": "openai",
            "model": "gpt-4.1",
            "input_tokens": "1000",
            "output_tokens": "500",
            "cached_tokens": "100",
            "tool_call_count": "2",
            "actual_cost_usd": "1.2345",
            "estimated_cost_usd": "1.50",
            "manual_note": "provider usage export",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/proposals/agents/{agent_id}"

    with main.db_connect() as db:
        usage = db.execute("SELECT * FROM usage_records WHERE scope_type='agent' AND scope_id=?", (agent_id,)).fetchone()
        assert usage["actual_cost_usd"] == 1.2345
        summary = main.agent_cost_summary(db, agent_id)
        assert summary["monthly_actual_spend_usd"] == 1.2345
        assert summary["actual_spend_usd"] == 1.2345
        assert summary["estimated_spend_usd"] == 1.5

    detail = client.get(f"/proposals/agents/{agent_id}")
    assert detail.status_code == 200
    assert "$1.23" in detail.text
    assert "provider usage export" in detail.text


def test_workflow_start_handoff_failed_completion_approval(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    proposal_id = client.post("/api/proposals", data={"title": "Fix bug"}).json()["id"]
    with main.db_connect() as db:
        db.execute(
            "UPDATE workflow_template_stages SET assigned_agent_id=?, handoff_agent_id=? WHERE template_id=? AND position=1",
            ("agent_architect", "agent_builder", "workflow_bug_triage"),
        )
        db.commit()

    start = client.post(
        "/api/workflows/start",
        data={"template_id": "workflow_bug_triage", "proposal_id": proposal_id},
        follow_redirects=False,
    )
    assert start.status_code == 303
    run_id = os.path.basename(start.headers["location"])

    with main.db_connect() as db:
        stages = db.execute("SELECT * FROM workflow_run_stages WHERE run_id=? ORDER BY position", (run_id,)).fetchall()
        assert len(stages) == 5
        first_stage = stages[0]["id"]
        assert stages[0]["assigned_agent_id"] == "agent_architect"
        assert stages[0]["handoff_agent_id"] == "agent_builder"

    client.post(f"/api/workflows/runs/{run_id}/handoffs", data={"from_agent_id": "agent_architect", "to_agent_id": "agent_builder", "reason": "Implementation ready", "context_summary": "Fix plan approved"})
    client.post(f"/api/workflows/runs/{run_id}/stages/{first_stage}", data={"status": "failed", "notes": "Cannot reproduce"})
    client.post(f"/api/workflows/runs/{run_id}/status", data={"status": "completed"})

    with main.db_connect() as db:
        handoff = db.execute("SELECT * FROM agent_handoffs WHERE workflow_run_id=?", (run_id,)).fetchone()
        assert handoff["status"] == "requested"
        approval = db.execute("SELECT * FROM approval_requests WHERE entity_type='workflow_run' AND entity_id=?", (run_id,)).fetchone()
        assert approval["title"] == "Complete workflow with failed stages"
        events = db.execute("SELECT event_type FROM audit_events WHERE entity_type='workflow_run' AND entity_id=?", (run_id,)).fetchall()
        assert "handoff_requested" in {r["event_type"] for r in events}


def test_approval_decision_and_existing_status_trigger(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    proposal_id = client.post("/api/proposals", data={"title": "Approve me"}).json()["id"]
    patch = client.patch(f"/api/proposals/{proposal_id}/status", data={"status": "approved"})
    assert patch.status_code == 200
    assert (tmp_path / "proposals_trigger").read_text() == f"APPROVED:{proposal_id}"

    with main.db_connect() as db:
        approval_id = main.create_approval(db, "proposal", proposal_id, "Manual review", "medium", "Test approval")
        db.commit()

    client.post(f"/api/approvals/{approval_id}/decision", data={"status": "approved", "decision_reason": "Looks good"})
    with main.db_connect() as db:
        approval = db.execute("SELECT status, decision_reason FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
        assert approval["status"] == "approved"
        assert approval["decision_reason"] == "Looks good"
