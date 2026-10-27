import pytest
from fastapi.testclient import TestClient

from backend.db.models import SessionRow, User
from backend.db.session import session
from backend.security import users
from tests.conftest import AUTH

PW = "correct horse battery"


@pytest.fixture()
def team(api):
    """Switch to team mode with an admin; ALWAYS restore local mode so other tests are unaffected."""
    from backend.main import app
    r = api.post("/api/auth/setup", json={"email": "root@example.test", "name": "Root", "password": PW})
    assert r.status_code == 200, r.text

    def client():
        return TestClient(app, base_url="http://localhost:8000")

    def signed_in(email, password=PW):
        c = client()
        res = c.post("/api/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, res.text
        c.headers["X-FlowPilot-CSRF"] = res.json()["csrf"]
        return c
    yield api, client, signed_in
    users.set_mode("local")
    with session() as s:
        s.query(SessionRow).delete()
        s.query(User).delete()


def test_team_mode_requires_login_and_hides_the_local_token(team):
    api, client, signed_in = team
    anon = client()
    assert anon.get("/api/session").json() == {"token": None, "mode": "users"}
    r = anon.get("/api/workflows")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "login_required"
    assert anon.get("/api/health").status_code == 200                               # liveness stays open
    assert api.raw.get("/api/workflows", headers=AUTH).status_code == 200            # the service token still works for automation
    assert anon.post("/api/auth/setup", json={"email": "x@example.test", "name": "x", "password": PW}).status_code in (401, 409)


def test_login_csrf_and_rate_limit(team):
    api, client, signed_in = team
    c = client()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "root@example.test", "password": "wrong-password"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "root@example.test", "password": PW}).status_code == 429   # locked out, even with the right password
    from backend.api import routes
    routes._fails.clear()
    admin = signed_in("root@example.test")
    assert admin.get("/api/auth/status").json()["user"]["role"] == "admin"
    no_csrf = client()
    no_csrf.cookies.update(admin.cookies)
    assert no_csrf.post("/api/workflows", json={"name": "csrf?"}).status_code == 403                             # cookie alone is not enough
    assert admin.post("/api/workflows", json={"name": "with csrf"}).status_code == 200
    assert admin.post("/api/auth/logout").status_code == 200
    assert admin.get("/api/workflows").status_code == 401                                                      # session is gone server-side


def test_roles_are_enforced_server_side(team):
    api, client, signed_in = team
    admin = signed_in("root@example.test")
    for email, role in (("view@example.test", "viewer"), ("edit@example.test", "editor")):
        assert admin.post("/api/users", json={"email": email, "name": role, "password": PW, "role": role}).status_code == 200
    viewer, editor = signed_in("view@example.test"), signed_in("edit@example.test")
    assert viewer.get("/api/workflows").status_code == 200 and viewer.get("/api/dashboard").status_code == 200
    assert viewer.post("/api/workflows", json={"name": "nope"}).status_code == 403
    assert editor.post("/api/workflows", json={"name": "editors may build"}).status_code == 200
    for path, method, body in (("/api/settings", "put", {"max_steps": 50}), ("/api/credentials/SLACK_WEBHOOK_URL", "put", {"value": "x"}), ("/api/users", "get", None),
                               ("/api/oauth/apps", "post", {"name": "ab", "auth_url": "https://a", "token_url": "https://b", "client_id": "c", "credential_name": "X_TOKEN"})):
        res = getattr(editor, method)(path, **({"json": body} if body else {}))
        assert res.status_code == 403 and res.json()["detail"]["code"] == "forbidden", path
    assert admin.put("/api/settings", json={"max_steps": 150}).status_code == 200
    assert admin.put("/api/settings", json={"max_steps": 200}).status_code == 200
    assert admin.post("/api/users", json={"email": "view@example.test", "name": "dup", "password": PW, "role": "viewer"}).status_code == 409
    assert admin.post("/api/users", json={"email": "short@example.test", "name": "s", "password": "short", "role": "viewer"}).status_code == 422


def test_last_admin_protection_and_session_invalidation(team):
    api, client, signed_in = team
    admin = signed_in("root@example.test")
    me = admin.get("/api/auth/status").json()["user"]["id"]
    assert admin.patch(f"/api/users/{me}", json={"role": "viewer"}).status_code == 409
    assert admin.patch(f"/api/users/{me}", json={"active": False}).status_code == 409
    admin.post("/api/users", json={"email": "e2@example.test", "name": "e2", "password": PW, "role": "editor"})
    e2 = signed_in("e2@example.test")
    uid = e2.get("/api/auth/status").json()["user"]["id"]
    assert admin.patch(f"/api/users/{uid}", json={"role": "viewer"}).status_code == 200
    assert e2.get("/api/workflows").status_code == 401                                   # privilege change forces re-login
    assert client().post("/api/auth/login", json={"email": "e2@example.test", "password": PW}).status_code == 200
    assert admin.patch(f"/api/users/{uid}", json={"active": False}).status_code == 200
    assert client().post("/api/auth/login", json={"email": "e2@example.test", "password": PW}).status_code == 401


def test_audit_and_approvals_record_the_human(team):
    api, client, signed_in = team
    from backend.execution.worker import wait_for_run
    from backend.workflows.examples import E, N
    from tests.helpers import END, START
    admin = signed_in("root@example.test")
    wid = admin.post("/api/workflows", json={"name": "paid", "definition": {"format": "flowpilot.workflow/v1", "policy": {}, "edges": [E("start", "m"), E("m", "end")],
                     "nodes": [START, N("m", "tool", 0, 0, "m", tool="mock.charge_card", args={"amount": 1, "customer": "team"}), END]}}).json()["id"]
    rid = admin.post(f"/api/workflows/{wid}/run", json={"input": {}}).json()["run_id"]
    wait_for_run(rid, 15, until={"waiting_for_approval"})
    aid = admin.get(f"/api/runs/{rid}").json()["approvals"][0]["id"]
    assert admin.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 200
    wait_for_run(rid, 15)
    ap = admin.get(f"/api/runs/{rid}").json()["approvals"][0]
    assert ap["decision"]["decided_by"] == "root@example.test"
    entries = admin.get("/api/audit?limit=20").json()["entries"]
    assert any(e["actor"] == "root@example.test" and e["path"].endswith("/decide") for e in entries)


def test_password_hashing_is_salted_and_verifies():
    a, b = users.hash_password(PW), users.hash_password(PW)
    assert a != b and users.verify_password(PW, a) and not users.verify_password("nope", a) and not users.verify_password(PW, "garbage")
