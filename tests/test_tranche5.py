import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import config
from backend.execution.worker import wait_for_run
from backend.workflows.examples import E, N
from tests.fakeserver import Fake
from tests.helpers import END, START, mk, run


@pytest.fixture()
def local_net(monkeypatch):
    monkeypatch.setattr(config, "HTTP_ALLOW_PRIVATE_HOSTS", {"127.0.0.1"})


def approve_and_wait(api, wid, inp=None):
    rid, r = run(api, wid, inp or {}, until={"waiting_for_approval", "succeeded", "failed"})
    if r["status"] == "waiting_for_approval":
        api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
        wait_for_run(rid, 20)
    return rid, api.get(f"/api/runs/{rid}").json()


def tool_wf(api, tool, args, **cfg):
    return mk(api, [START, N("t", "tool", 0, 0, "t", tool=tool, args=args, **cfg), N("end", "end", 0, 0, "e", output="{{ nodes.t }}")], [E("start", "t"), E("t", "end")])


# ---------------------------------------------------------------- memory
def test_agent_memory_persists_across_runs_and_is_skipped_in_replay(api):
    llm = N("a", "llm", 0, 0, "a", provider="mock", model="mock-agent-1", prompt="{{ input.msg }}", memory={"enabled": True, "window": 4, "key": "{{ input.session }}"})
    wid = mk(api, [START, llm, N("end", "end", 0, 0, "e", output="{{ nodes.a.text }}")], [E("start", "a"), E("a", "end")])
    for m in ("hello there", "second message"):
        rid, r = run(api, wid, {"msg": m, "session": "s1"})
    ev = api.get(f"/api/runs/{rid}/events").json()["events"]
    assert next(e for e in ev if e["type"] == "memory.loaded")["data"]["messages"] == 2      # saw run 1's exchange
    _, other = run(api, wid, {"msg": "hi", "session": "s2"})
    assert next(e for e in api.get(f"/api/runs/{other['id']}/events").json()["events"] if e["type"] == "memory.loaded")["data"]["messages"] == 0   # isolated by key
    mem = api.get("/api/memory?key=s1").json()
    assert [m["role"] for m in mem] == ["user", "assistant", "user", "assistant"]
    case = api.post("/api/lab/cases", json={"run_id": rid}).json()["id"]
    res = api.post(f"/api/lab/cases/{case}/replay", json={}).json()
    assert any(e["type"] == "memory.skipped_in_replay" for e in api.get(f"/api/runs/{res['run_id']}/events").json()["events"])
    assert len(api.get("/api/memory?key=s1").json()) == 4                                     # replay wrote nothing
    assert api.delete(f"/api/memory?scope={mem[0]['scope']}&key=s1").json()["deleted"] == 4


# ---------------------------------------------------------------- retrieval
def _kb(api, name, mode, text="Refunds are issued within 14 days of purchase.\n\nShipping takes 3-5 business days."):
    kid = api.post("/api/knowledge", json={"name": name, "embedding": mode}).json()["id"]
    assert api.post(f"/api/knowledge/{kid}/documents", json={"title": "Policy", "text": text}).status_code == 200
    return kid


def test_ngram_retrieval_tolerates_typos_while_lexical_does_not(api):
    lex, ng = _kb(api, "kb-lex", "lexical"), _kb(api, "kb-ngram", "ngram")
    assert api.post(f"/api/knowledge/{lex}/search", json={"query": "refnds polcy"}).json()["results"] == []
    hits = api.post(f"/api/knowledge/{ng}/search", json={"query": "refnds polcy"}).json()["results"]
    assert hits and "Refunds" in hits[0]["text"] and hits[0]["mode"] == "ngram"


def test_remote_embeddings_endpoint(api, monkeypatch, local_net):
    vocab = ["refund", "ship", "days"]
    fake = Fake(lambda rec: {"data": [{"embedding": [float(t.lower().count(w)) for w in vocab]} for t in json.loads(rec["body"])["input"]]})
    monkeypatch.setenv("EMBEDDINGS_URL", fake.url + "/v1/embeddings")
    kid = _kb(api, "kb-remote", "remote")
    r = api.post(f"/api/knowledge/{kid}/search", json={"query": "refund days"}).json()["results"]
    assert r and r[0]["mode"] == "remote" and "Refunds" in r[0]["text"]
    assert len(fake.requests) >= 2                                              # documents + query were embedded remotely
    monkeypatch.delenv("EMBEDDINGS_URL")
    bad = api.post("/api/knowledge", json={"name": "kb-remote2", "embedding": "remote"}).json()["id"]
    assert api.post(f"/api/knowledge/{bad}/documents", json={"title": "x", "text": "y y"}).status_code == 422   # clear setup message, nothing stored
    fake.close()


# ---------------------------------------------------------------- connectors
def test_sigv4_matches_aws_published_vector():
    from backend.tools.connectors import sigv4_headers
    h = sigv4_headers("GET", "examplebucket.s3.amazonaws.com", "/test.txt", "", {"range": "bytes=0-9"}, b"", "us-east-1", "s3",
                      "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


def test_stripe_idempotency_header_notion_airtable_s3_and_no_secret_in_trace(api, monkeypatch, local_net):
    fake = Fake(lambda rec: {"id": "pi_123", "status": "requires_payment_method", "url": "https://n.example/p", "updates": {"updatedRows": 1}})
    for env, val in {"STRIPE_BASE_URL": fake.url, "STRIPE_API_KEY": "sk_test_ABCDEFGHIJKLMNOPQRSTUV", "NOTION_BASE_URL": fake.url, "NOTION_TOKEN": "secret_notiontokenvalue1234",
                     "AIRTABLE_BASE_URL": fake.url, "AIRTABLE_TOKEN": "patAIRTABLEtokenvalue1234", "S3_ENDPOINT": fake.url, "AWS_ACCESS_KEY_ID": "AKIATESTTESTTESTTEST", "AWS_SECRET_ACCESS_KEY": "s3secretsecretsecret12345"}.items():
        monkeypatch.setenv(env, val)
    rid, r = approve_and_wait(api, tool_wf(api, "stripe.create_payment_intent", {"amount_cents": 1999, "currency": "usd"}, idempotency_key="{{ run.id }}-pay"))
    assert r["status"] == "succeeded" and r["output"]["result"]["payment_intent"] == "pi_123"
    req = fake.requests[-1]
    assert req["path"] == "/v1/payment_intents" and req["headers"]["idempotency-key"] == f"{rid}-pay" and req["headers"]["authorization"].startswith("Bearer sk_test_")
    assert "amount=1999" in req["body"]
    assert "sk_test_ABCDEFGHIJ" not in json.dumps(r) and "sk_test_ABCDEFGHIJ" not in json.dumps(api.get(f"/api/runs/{rid}/events").json())
    _, r = approve_and_wait(api, tool_wf(api, "notion.create_page", {"database_id": "db1", "title": "Hello"}))
    assert r["status"] == "succeeded" and json.loads(fake.requests[-1]["body"])["properties"]["Name"]["title"][0]["text"]["content"] == "Hello"
    assert fake.requests[-1]["headers"]["notion-version"]
    _, r = approve_and_wait(api, tool_wf(api, "airtable.create_record", {"base_id": "appABCDEFGH", "table": "Leads", "fields": {"Name": "Ada"}}))
    assert r["status"] == "succeeded" and json.loads(fake.requests[-1]["body"]) == {"fields": {"Name": "Ada"}}
    _, r = approve_and_wait(api, tool_wf(api, "s3.put_object", {"bucket": "my-bucket", "key": "a/b.txt", "content": "hello"}))
    assert r["status"] == "succeeded"
    s3 = fake.requests[-1]
    assert s3["method"] == "PUT" and s3["path"] == "/my-bucket/a/b.txt" and "AWS4-HMAC-SHA256 Credential=AKIATESTTESTTESTTEST/" in s3["headers"]["authorization"]
    import hashlib
    assert s3["headers"]["x-amz-content-sha256"] == hashlib.sha256(b"hello").hexdigest()
    fake.close()


def test_sqlite_query_is_read_only_and_confined(api, monkeypatch, tmp_path):
    db = tmp_path / "data.db"
    con = sqlite3.connect(db); con.execute("create table t (a int, b text)"); con.execute("insert into t values (1,'x'),(2,'y')"); con.commit(); con.close()
    monkeypatch.setenv("FLOWPILOT_SQL_DIR", str(tmp_path))
    _, r = run(api, tool_wf(api, "sqlite.query", {"database": "data.db", "sql": "select a, b from t where a > ? order by a", "params": [0]}))
    assert r["status"] == "succeeded" and r["output"]["result"]["rows"] == [[1, "x"], [2, "y"]]
    for sql in ["delete from t", "select 1; drop table t", "with x as (select 1) insert into t values (9,'z')", "pragma writable_schema=1"]:
        _, r = run(api, tool_wf(api, "sqlite.query", {"database": "data.db", "sql": sql}))
        assert r["status"] == "failed" and r["error"]["code"] == "tool_forbidden", sql
    _, r = run(api, tool_wf(api, "sqlite.query", {"database": "../etc/passwd", "sql": "select 1"}))
    assert r["status"] == "failed"
    assert sqlite3.connect(db).execute("select count(*) from t").fetchone()[0] == 2      # nothing was modified


# ---------------------------------------------------------------- vault
def test_vault_encrypts_never_returns_values_and_feeds_connectors(api, monkeypatch, local_net):
    fake = Fake()
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    wid = tool_wf(api, "slack.post_message", {"text": "via vault"})
    _, r = approve_and_wait(api, wid)
    assert r["status"] == "failed" and "Credentials" in r["error"]["message"]                 # points the user at the vault
    secret_url = fake.url + "/hooks/TOPSECRETTOKEN123"
    assert api.put("/api/credentials/SLACK_WEBHOOK_URL", json={"value": secret_url}).status_code == 200
    assert api.raw.put("/api/credentials/SLACK_WEBHOOK_URL", json={"value": "x"}).status_code == 401
    assert api.put("/api/credentials/bad name", json={"value": "x"}).status_code in (404, 422)
    listing = api.get("/api/credentials").json()
    assert listing == [{"name": "SLACK_WEBHOOK_URL", "kind": "secret", "meta": {}, "updated_at": listing[0]["updated_at"]}]
    assert "TOPSECRET" not in json.dumps(listing)
    from backend.db.session import session
    from backend.db.models import Credential
    with session() as s:
        assert "TOPSECRET" not in s.get(Credential, "SLACK_WEBHOOK_URL").ciphertext             # encrypted at rest
    rid, r = approve_and_wait(api, wid)
    assert r["status"] == "succeeded" and json.loads(fake.requests[-1]["body"]) == {"text": "via vault"}
    assert "TOPSECRET" not in json.dumps(r) + json.dumps(api.get(f"/api/runs/{rid}/events").json())
    assert any(t["name"] == "slack.post_message" and t["configured"] for t in api.get("/api/meta").json()["tools"])
    assert api.delete("/api/credentials/SLACK_WEBHOOK_URL").status_code == 200
    fake.close()


def test_oauth_pkce_flow_token_storage_and_refresh(api, monkeypatch, local_net):
    issued = {"n": 0}

    def token_reply(rec):
        form = parse_qs(rec["body"])
        issued["n"] += 1
        if form["grant_type"] == ["authorization_code"]:
            assert form["code"] == ["the-code"] and form["code_verifier"][0]
            return {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["refresh-1"]
        return {"access_token": "access-2", "expires_in": 3600}
    idp = Fake(token_reply)
    r = api.post("/api/oauth/apps", json={"name": "gsheets", "auth_url": idp.url + "/authorize", "token_url": idp.url + "/token", "client_id": "cid", "client_secret": "csecret",
                                          "scopes": "spreadsheets", "credential_name": "GOOGLE_SHEETS_TOKEN"})
    assert r.status_code == 200
    url = api.post("/api/oauth/apps/gsheets/start").json()["url"]
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cid"] and q["redirect_uri"][0].endswith("/api/oauth/callback")
    cb = api.raw.get("/api/oauth/callback", params={"state": q["state"][0], "code": "the-code"}, follow_redirects=False)
    assert cb.status_code in (302, 307) and "connected=gsheets" in cb.headers["location"]
    again = api.raw.get("/api/oauth/callback", params={"state": q["state"][0], "code": "the-code"}, follow_redirects=False)
    assert "oauth_error" in again.headers["location"]                                          # state is single-use
    assert "oauth_error" in api.raw.get("/api/oauth/callback", params={"state": "forged", "code": "x"}, follow_redirects=False).headers["location"]
    from backend.security import vault
    assert vault.get("GOOGLE_SHEETS_TOKEN") == "access-1"
    from backend.db.models import Credential
    from backend.db.session import session
    with session() as s:                                                                         # force expiry -> automatic refresh
        c = s.get(Credential, "GOOGLE_SHEETS_TOKEN"); c.meta = {**c.meta, "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}
    assert vault.get("GOOGLE_SHEETS_TOKEN") == "access-2"
    sheets = Fake({"updates": {"updatedRows": 1}})
    monkeypatch.setenv("SHEETS_BASE_URL", sheets.url)
    _, run_ = approve_and_wait(api, tool_wf(api, "sheets.append_row", {"spreadsheet_id": "1AbCdEfGhIjKlMnOp", "values": ["a", 1]}))
    assert run_["status"] == "succeeded" and sheets.requests[-1]["headers"]["authorization"] == "Bearer access-2"
    assert "access-2" not in json.dumps(api.get("/api/oauth/apps").json()) + json.dumps(api.get("/api/credentials").json())
    assert api.delete("/api/oauth/apps/gsheets").status_code == 200
    idp.close(); sheets.close()
