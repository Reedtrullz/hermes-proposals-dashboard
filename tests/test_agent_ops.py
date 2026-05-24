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
        project_cols = {r["name"] for r in db.execute("PRAGMA table_info(projects)").fetchall()}
        assert {"goal_id", "parent_id", "assigned_agent_id", "acceptance_criteria_json", "risk_level", "estimated_cost_usd", "actual_cost_usd", "is_demo"} <= proposal_cols
        assert {"assigned_agent_id", "handoff_agent_id"} <= template_stage_cols
        assert {"assigned_agent_id", "handoff_agent_id"} <= run_stage_cols
        assert "actual_cost_usd" in usage_cols
        assert {"id", "name", "description", "desired_outcome", "status"} <= project_cols
        assert db.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"] >= 6
        assert db.execute("SELECT COUNT(*) AS n FROM workflow_templates").fetchone()["n"] == 3
        assert db.execute("SELECT COUNT(*) AS n FROM approval_policies").fetchone()["n"] == 4


def test_health_endpoint_bypasses_auth(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "ok"}


def test_existing_proposal_api_and_agent_goal_linking(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    created = client.post("/api/proposals", data={"title": "Build agent ops", "body": "Keep proposals central", "board": "default"}).json()
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
    assert detail["status"] == "waiting"
    assert detail["goal_id"] == goal_id
    assert detail["assigned_agent_id"] == agent_id
    assert detail["risk_level"] == "critical"
    assert detail["criteria"] == ["Goal breadcrumb is visible", "Agent is assigned"]
    assert detail["cost_total"] == 3.5

    with main.db_connect() as db:
        approvals = db.execute("SELECT title FROM approval_requests WHERE entity_type='proposal' AND entity_id=?", (proposal_id,)).fetchall()
        titles = {r["title"] for r in approvals}
        assert "Critical-risk proposal requires approval" in titles
        assert "Cost threshold exceeded" in titles
        events = db.execute("SELECT event_type FROM audit_events WHERE entity_type='proposal' AND entity_id=?", (proposal_id,)).fetchall()
        assert "proposal_metadata_updated" in {r["event_type"] for r in events}


def test_proposals_page_renders_first_use_and_settings_navigation(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    client.post("/api/proposals", data={"title": "Rendered card"})

    response = client.get("/proposals")

    assert response.status_code == 200
    assert "Rendered card" in response.text
    assert "Try demo" in response.text
    assert 'action="/proposals"' in response.text
    assert 'for="new-proposal-title"' in response.text
    assert "Waiting for worker" in response.text
    assert 'href="/proposals/workflows"' in response.text
    assert 'href="/proposals/approvals"' in response.text
    assert 'href="/proposals/projects"' in response.text
    assert 'href="/proposals/settings"' in response.text


def test_prefixed_ops_pages_and_api_routes_work_under_proposals(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    for path in ["/proposals/projects", "/proposals/settings", "/proposals/setup", "/proposals/goals", "/proposals/agents", "/proposals/workflows", "/proposals/approvals", "/proposals/budgets"]:
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
        assert db.execute("SELECT status FROM proposals WHERE id=?", (proposal_id,)).fetchone()["status"] == "approved"


def test_executor_type_column_exists_and_defaults_to_hermes(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with main.db_connect() as db:
        cols = {r["name"] for r in db.execute("PRAGMA table_info(agents)").fetchall()}
        assert "executor_type" in cols
        agent = db.execute("SELECT executor_type FROM agents WHERE id='agent_builder'").fetchone()
        assert agent["executor_type"] == "hermes"


def test_create_codex_agent_from_template_has_correct_executor(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    resp = client.post("/api/proposals/agents/from-template",
        data={"template_key": "codex_coder"}, follow_redirects=False)
    assert resp.status_code == 303
    agent_id = resp.headers["location"].split("agent_id=", 1)[1]
    with main.db_connect() as db:
        agent = db.execute("SELECT executor_type, provider, model_name, system_prompt, tools_allowed_json FROM agents WHERE id=?", (agent_id,)).fetchone()
        assert agent["executor_type"] == "codex"
        assert agent["provider"] == "openai"
        assert "proposal_complete" in agent["system_prompt"]
        assert main.loads(agent["tools_allowed_json"], []) == ["comment", "proposal_complete", "proposal_heartbeat", "proposal_block"]


def test_executor_api_endpoint_returns_null_for_hermes_agent(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    # Create a proposal assigned to agent_builder (hermes executor)
    pid = client.post("/api/proposals", data={
        "title": "Executor test", "assigned_agent_id": "agent_builder"
    }).json()["id"]
    resp = client.get(f"/api/proposals/{pid}/executor")
    assert resp.status_code == 200
    data = resp.json()
    assert data["proposal_id"] == pid
    assert data["executor"] is None  # hermes = no executor info


def test_trigger_executor_file_written_for_codex_agent(tmp_path, monkeypatch):
    import json
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
    assert executor_file.exists(), "executor file should exist for codex agent"
    data = json.loads(executor_file.read_text())
    assert data["executor_type"] == "codex"
    assert data["proposal_id"] == pid

    # Hermes agent should NOT write executor file
    executor_file.unlink()
    client.post("/api/proposals", data={
        "title": "Hermes task", "assigned_agent_id": "agent_builder"
    })
    assert not executor_file.exists(), "executor file should NOT exist for hermes agent"


def test_dangerous_executor_triggers_approval(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    # Create codex agent
    client.post("/api/agents", data={
        "name": "YOLO Coder", "role_title": "Coder",
        "provider": "openai", "model_name": "gpt-5",
        "executor_type": "codex", "tools_allowed": "comment",
        "monthly_budget_usd": "50",
    })
    with main.db_connect() as db:
        agent_id = db.execute("SELECT id FROM agents WHERE name='YOLO Coder'").fetchone()["id"]
    # Create proposal assigned to codex agent
    pid = client.post("/api/proposals", data={
        "title": "Risky task", "assigned_agent_id": agent_id
    }).json()["id"]
    # Approve it — triggers ensure_policy_approvals
    client.patch(f"/api/proposals/{pid}/status", data={"status": "approved"})
    with main.db_connect() as db:
        approvals = db.execute(
            "SELECT title FROM approval_requests WHERE entity_type='proposal' AND entity_id=?",
            (pid,)
        ).fetchall()
        titles = {r["title"] for r in approvals}
        assert "Dangerous executor (Codex CLI) requires approval" in titles


def test_browser_create_redirects_once_to_waiting_proposal(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/proposals",
        data={"title": "Review onboarding", "body": "Make first use understandable"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/proposals/")
    proposal_id = location.rsplit("/", 1)[1]
    assert (tmp_path / "proposals_trigger").read_text() == proposal_id
    with main.db_connect() as db:
        rows = db.execute("SELECT status, is_demo FROM proposals WHERE title='Review onboarding'").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "waiting"
        assert rows[0]["is_demo"] == 0

    detail = client.get(location)
    assert "Waiting for worker" in detail.text
    assert "requires a configured Hermes or CLI worker" in detail.text
    assert "analyzing" not in detail.text.lower()


def test_demo_walkthrough_is_idempotent_and_reset_only_removes_demo_data(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    real_id = client.post("/api/proposals", data={"title": "Keep me"}).json()["id"]
    (tmp_path / "proposals_trigger").unlink()

    first = client.post("/proposals/demo", follow_redirects=False)
    second = client.post("/proposals/demo", follow_redirects=False)

    assert first.status_code == 303
    assert second.headers["location"] == first.headers["location"]
    demo_id = first.headers["location"].rsplit("/", 1)[1]
    assert not (tmp_path / "proposals_trigger").exists()
    with main.db_connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM proposals WHERE is_demo=1").fetchone()["n"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM proposal_comments WHERE proposal_id=?", (demo_id,)).fetchone()["n"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM approval_requests WHERE entity_type='proposal' AND entity_id=? AND status='pending'", (demo_id,)).fetchone()["n"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM workflow_runs WHERE proposal_id=?", (demo_id,)).fetchone()["n"] == 1

    detail = client.get(first.headers["location"])
    assert "DEMO" in detail.text
    assert "No worker is executed" in detail.text
    assert "Needs decision" in detail.text
    decision = client.post(f"/api/proposals/{demo_id}/status", data={"status": "approved"}, follow_redirects=False)
    assert decision.status_code == 303
    assert not (tmp_path / "proposals_trigger").exists()

    reset = client.post("/proposals/demo/reset", follow_redirects=False)
    assert reset.headers["location"] == "/proposals"
    with main.db_connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM proposals WHERE is_demo=1").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM proposals WHERE id=?", (real_id,)).fetchone()["n"] == 1
        assert db.execute("SELECT COUNT(*) AS n FROM workflow_templates").fetchone()["n"] == 3
        assert db.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"] >= 6


def test_browser_note_route_escapes_markup_and_renders_limited_formatting(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    proposal_id = client.post("/api/proposals", data={"title": "Safe notes"}).json()["id"]

    response = client.post(
        f"/proposals/{proposal_id}/notes",
        data={"author": "user", "body": "<script>alert(1)</script> **confirmed** and `ready`"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<strong>confirmed</strong>" in page
    assert "<code>ready</code>" in page


def test_proposal_decision_resolves_pending_review_consistently(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    proposal_id = client.post("/api/proposals", data={"title": "Needs decision"}).json()["id"]
    with main.db_connect() as db:
        main.create_approval(db, "proposal", proposal_id, "User decision", "medium", "Review before accepting")
        db.commit()

    response = client.post(f"/api/proposals/{proposal_id}/status", data={"status": "rejected"}, follow_redirects=False)
    assert response.status_code == 303
    with main.db_connect() as db:
        assert db.execute("SELECT status FROM proposals WHERE id=?", (proposal_id,)).fetchone()["status"] == "rejected"
        assert db.execute("SELECT status FROM approval_requests WHERE entity_id=?", (proposal_id,)).fetchone()["status"] == "rejected"


def test_project_create_lists_scoped_proposals_and_local_recommendations(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    created = client.post(
        "/proposals/projects",
        data={
            "name": "Client Portal",
            "description": "Consolidate account management",
            "desired_outcome": "Customers can manage access without support",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    project_url = created.headers["location"]
    project_id = project_url.rsplit("/", 1)[1]

    proposal = client.post(
        "/proposals",
        data={"title": "Define permissions", "body": "Specify roles", "board": project_id},
        follow_redirects=False,
    )
    assert proposal.status_code == 303

    overview = client.get("/proposals/projects").text
    assert "Client Portal" in overview
    assert "Recommended next: Route 1 waiting proposal" in overview
    assert "No AI model is called automatically" in overview

    detail = client.get(project_url).text
    assert "Define permissions" in detail
    assert "Suggested next steps" in detail
    assert "Route 1 waiting proposal" in detail
    assert "no external model generated this list" in detail


def test_legacy_board_api_creates_visible_project_and_metadata_can_reassign(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)

    proposal_id = client.post(
        "/api/proposals",
        data={"title": "Legacy project proposal", "board": "older-project"},
    ).json()["id"]
    overview = client.get("/proposals/projects").text
    assert "older-project" in overview

    project = client.post("/proposals/projects", data={"name": "New Home"}, follow_redirects=False)
    new_project_id = project.headers["location"].rsplit("/", 1)[1]
    client.post(
        f"/api/proposals/{proposal_id}/metadata",
        data={"board": new_project_id, "risk_level": "low"},
    )
    page = client.get(f"/api/proposals/{proposal_id}").json()
    assert page["board"] == new_project_id


def test_project_worker_recommendation_creates_waiting_scoped_proposal(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    project = client.post(
        "/proposals/projects",
        data={"name": "Website Refresh", "desired_outcome": "Publish a clearer homepage"},
        follow_redirects=False,
    )
    project_id = project.headers["location"].rsplit("/", 1)[1]

    request = client.post(f"/proposals/projects/{project_id}/planning", follow_redirects=False)
    assert request.status_code == 303
    proposal_id = request.headers["location"].rsplit("/", 1)[1]
    with main.db_connect() as db:
        proposal = db.execute("SELECT title, body, board, status FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        assert proposal["title"] == "Recommend next steps for Website Refresh"
        assert proposal["board"] == project_id
        assert proposal["status"] == "waiting"
        assert "Publish a clearer homepage" in proposal["body"]
    assert (tmp_path / "proposals_trigger").read_text() == proposal_id


def test_completed_project_recommends_defining_next_milestone(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(main.app)
    project = client.post(
        "/proposals/projects",
        data={"name": "Released App", "desired_outcome": "The release is available"},
        follow_redirects=False,
    )
    project_id = project.headers["location"].rsplit("/", 1)[1]
    proposal_id = client.post(
        "/api/proposals",
        data={"title": "Ship release", "board": project_id},
    ).json()["id"]
    client.patch(f"/api/proposals/{proposal_id}/status", data={"status": "implemented"})

    detail = client.get(f"/proposals/projects/{project_id}").text
    assert "Choose the next project milestone" in detail
