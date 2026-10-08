"""Isolated security acceptance for Issue #2. No live issuer or production DB."""
import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
import pytest

import dashboard_auth as auth

OWNER_ID = "123456"
OWNER_EMAIL = "owner@example.test"
API_KEY = "K" * 48
ISSUER = "https://login.example.test"


def session(uid=OWNER_ID, email=OWNER_EMAIL, seconds=3600):
    return {"user": {"id": uid, "email": email},
            "expires": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}


def stub(monkeypatch, handler):
    monkeypatch.setattr(auth, "_new_issuer_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=2.0,
        trust_env=False, follow_redirects=False))


def load_app(tmp_path, monkeypatch, auth_enabled="1", owner=True, key=True):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_REQUIRE_AUTH", auth_enabled)
    monkeypatch.setenv("AUTH_URL", ISSUER)
    if owner:
        monkeypatch.setenv("OWNER_GITHUB_ID", OWNER_ID)
        monkeypatch.setenv("OWNER_EMAIL", OWNER_EMAIL)
    else:
        monkeypatch.delenv("OWNER_GITHUB_ID", raising=False)
        monkeypatch.delenv("OWNER_EMAIL", raising=False)
    if key:
        monkeypatch.setenv("HERMES_API_KEY", API_KEY)
    else:
        monkeypatch.delenv("HERMES_API_KEY", raising=False)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_issuer_config_and_cookie_chunk_validation():
    assert auth.trusted_issuer_origin(ISSUER) == ISSUER
    for value in ["http://login.example.test", "https://evil@host.test",
                  "https://host.test/path", "https://host.test:bad",
                  "https://host.test?x=1"]:
        assert auth.trusted_issuer_origin(value) is None
    assert auth.session_cookie_header(["irrelevant=secret; authjs.session-token.1=B; authjs.session-token.0=A"]) == (
        "authjs.session-token.0=A; authjs.session-token.1=B")
    for cookies in [["authjs.session-token=base; authjs.session-token.0=part"],
                    ["authjs.session-token.1=missing-zero"],
                    ["authjs.session-token.0=x; authjs.session-token.0=y"],
                    ["__Secure-authjs.session-token=x; authjs.session-token=y"],
                    ["irrelevant=1"]]:
        assert auth.session_cookie_header(cookies) is None


def test_machine_policy_is_explicit_and_never_admin():
    assert auth.machine_key_valid([API_KEY], API_KEY)
    assert not auth.machine_key_valid(["hermes-local"], "hermes-local")
    assert not auth.machine_key_valid([API_KEY, API_KEY], API_KEY)
    assert not auth.machine_key_valid(["wrong"], API_KEY)
    for verb, path in [("GET", "/api/proposals/p1/executor"),
                       ("GET", "/api/agents/agent_builder/executor-status"),
                       ("GET", "/api/agents/executor-summary"),
                       ("POST", "/api/proposals/p1/comments")]:
        assert auth.machine_route_allowed(verb, path)
    for verb, path in [("POST", "/api/proposals"), ("PATCH", "/api/proposals/p1/status"),
                       ("POST", "/api/workflows/start"), ("GET", "/api/proposals"),
                       ("POST", "/api/approvals/a/decision"),
                       ("GET", "/proposals/projects")]:
        assert not auth.machine_route_allowed(verb, path)


def test_verified_session_uses_only_auth_cookie_and_fixed_issuer(monkeypatch):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=session())
    stub(monkeypatch, handler)
    assert asyncio.run(auth.verify_owner_session(
        ["noise=secret; authjs.session-token.0=A; authjs.session-token.1=B"],
        ISSUER, OWNER_ID, OWNER_EMAIL))
    assert len(calls) == 1
    assert str(calls[0].url) == ISSUER + "/api/auth/session"
    assert calls[0].headers["cookie"] == "authjs.session-token.0=A; authjs.session-token.1=B"
    assert "secret" not in calls[0].headers["cookie"]


@pytest.mark.parametrize("reply", [
    httpx.Response(302, headers={"location": "https://evil.test"}),
    httpx.Response(500, text="error"),
    httpx.Response(200, text="not-json"),
    httpx.Response(200, json={"expires": "2099-01-01T00:00:00Z"}),
    httpx.Response(200, json=session(uid="attacker")),
    httpx.Response(200, json=session(email="other@example.test")),
    httpx.Response(200, json=session(seconds=-5)),
    httpx.Response(200, json=session(), headers={"content-type": "text/plain"}),
    httpx.Response(200, content=b"X" * 65537, headers={"content-type": "application/json"}),
])
def test_invalid_session_responses_fail_closed(monkeypatch, reply):
    stub(monkeypatch, lambda req: reply)
    assert not asyncio.run(auth.verify_owner_session(
        ["authjs.session-token=test"], ISSUER, OWNER_ID, OWNER_EMAIL))


def test_auth_on_unconfigured_fails_closed_and_preserves_state(tmp_path, monkeypatch):
    main = load_app(tmp_path, monkeypatch, owner=False, key=False)
    stub(monkeypatch, lambda req: pytest.fail("missing owner must not contact issuer"))
    client = TestClient(main.app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/proposals").status_code == 401
    assert client.post("/api/workflows/start", data={"template_id": "x"}).status_code == 401
    assert not main.TRIGGER_FILE.exists()


def test_machine_does_not_inherit_owner_privileges(tmp_path, monkeypatch):
    main = load_app(tmp_path, monkeypatch)
    stub(monkeypatch, lambda req: pytest.fail("machine must not contact issuer"))
    client = TestClient(main.app)
    h = {"X-Hermes-Key": API_KEY}
    assert client.get("/api/proposals/p1/executor", headers=h).status_code == 200
    with main.db_connect() as db:
        before = db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    for verb, path in [("POST", "/api/proposals"),
                       ("POST", "/api/workflows/start"),
                       ("POST", "/api/approvals/id/decision"),
                       ("GET", "/proposals/projects")]:
        resp = client.request(verb, path, headers=h, follow_redirects=False)
        assert resp.status_code == 403
    with main.db_connect() as db:
        assert db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == before
    assert not main.TRIGGER_FILE.exists()


def test_browser_owner_requires_verified_issuer_identity(tmp_path, monkeypatch):
    main = load_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    client.cookies.set("authjs.session-token", "arbitrary")
    stub(monkeypatch, lambda req: httpx.Response(200, json=session(uid="stranger")))
    assert client.get("/api/proposals").status_code == 401
    stub(monkeypatch, lambda req: httpx.Response(200, json=session()))
    assert client.get("/api/proposals").status_code == 200
    assert client.get("/proposals/projects").status_code == 200


def test_auth_off_only_local_host(tmp_path, monkeypatch):
    main = load_app(tmp_path, monkeypatch, auth_enabled="0")
    client = TestClient(main.app)
    assert client.get("/proposals/projects").status_code == 200
    assert client.get("/proposals/projects", headers={"Host": "public.example.test"}).status_code == 403


def test_machine_denied_every_non_allowlisted_mutation(tmp_path, monkeypatch):
    main = load_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    for route in main.app.routes:
        path = route.path
        for key in ("proposal_id", "project_id", "agent_id", "goal_id", "run_id",
                    "stage_id", "approval_id", "template_id", "handoff_id", "budget_id"):
            path = path.replace("{" + key + "}", "test-id")
        for verb in route.methods or []:
            if verb in {"POST", "PATCH", "PUT", "DELETE"} and not auth.machine_route_allowed(verb, path):
                resp = client.request(verb, path, headers={"X-Hermes-Key": API_KEY},
                                      follow_redirects=False)
                assert resp.status_code == 403, (verb, path, resp.status_code)
    assert not main.TRIGGER_FILE.exists()
    assert not main.TRIGGER_EXECUTOR_FILE.exists()
