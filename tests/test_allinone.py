import http.server
import json
import threading
from datetime import datetime, timezone

import pytest

from backend.execution import scheduler
from backend.execution.worker import wait_for_run
from backend.workflows import cron
from backend.workflows.examples import E, N
from tests.helpers import END, START, mk, run


def test_cron_parser_and_next_fire():
    t = datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)   # Thursday
    assert cron.matches("30 9 * * 4", t) and cron.matches("*/15 9-10 * * *", t) and not cron.matches("31 9 * * *", t)
    assert cron.next_fire("0 12 * * *", t).isoformat().startswith("2026-10-22T12:00")
    for bad in ["* * * *", "61 * * * *", "a b c d e", "*/0 * * * *", "5-1 * * * *"]:
        with pytest.raises(cron.CronError):
            cron.parse(bad)


def test_schedule_fires_once_per_minute_and_validates(api):
    start = N("start", "start", 0, 0, "s", trigger="schedule", cron="*/5 * * * *", schedule_input={"hello": "world"})
    wid = mk(api, [start, END], [E("start", "end")])
    assert api.post(f"/api/workflows/{wid}/schedule", json={"enabled": True}).status_code == 200
    t = datetime(2026, 10, 22, 10, 5, tzinfo=timezone.utc)
    first = [r for r in scheduler.tick(t)]
    assert len(first) >= 1
    mine = [r for r in first if api.get(f"/api/runs/{r}").json()["workflow_id"] == wid]
    assert len(mine) == 1
    assert [r for r in scheduler.tick(t) if api.get(f"/api/runs/{r}").json()["workflow_id"] == wid] == []   # same minute: no double fire
    assert wait_for_run(mine[0], 10) == "succeeded"
    r = api.get(f"/api/runs/{mine[0]}").json()
    assert r["trigger"] == "schedule" and r["input"] == {"hello": "world"}
    assert not [x for x in scheduler.tick(datetime(2026, 10, 22, 10, 6, tzinfo=timezone.utc)) if api.get(f"/api/runs/{x}").json()["workflow_id"] == wid]
    bad = mk(api, [N("start", "start", 0, 0, "s", trigger="schedule", cron="nope"), END], [E("start", "end")])
    assert api.post(f"/api/workflows/{bad}/schedule", json={"enabled": True}).status_code in (409, 422)
    assert any("cron" in i["message"].lower() for i in api.get(f"/api/workflows/{bad}").json()["issues"])


def test_publish_as_api(api):
    wid = mk(api, [START, N("t", "transform", 0, 0, "t", op="template", template="hi {{ input.name }}"), END], [E("start", "t"), E("t", "end")])
    assert api.raw.post(f"/api/v1/workflows/{wid}/invoke", json={"input": {}}).status_code == 401
    tok = api.post(f"/api/workflows/{wid}/publish-token").json()["token"]
    assert api.raw.post(f"/api/v1/workflows/{wid}/invoke", json={"input": {}}, headers={"X-FlowPilot-Api-Key": "bad"}).status_code == 401
    r = api.raw.post(f"/api/v1/workflows/{wid}/invoke", json={"input": {"name": "ada"}, "wait_seconds": 10}, headers={"X-FlowPilot-Api-Key": tok}).json()
    assert r["status"] == "succeeded" and r["output"]["result"]["t"]["value"] == "hi ada"
    assert api.delete(f"/api/workflows/{wid}/publish-token").status_code == 200
    assert api.raw.post(f"/api/v1/workflows/{wid}/invoke", json={"input": {}}, headers={"X-FlowPilot-Api-Key": tok}).status_code == 401


def test_knowledge_base_rag_end_to_end(api):
    kid = api.post("/api/knowledge", json={"name": "handbook"}).json()["id"]
    api.post(f"/api/knowledge/{kid}/documents", json={"title": "Refunds", "text": "Refunds are issued within 14 days of purchase. Contact support with your order number.\n\nShipping takes 3-5 business days."})
    api.post(f"/api/knowledge/{kid}/documents", json={"title": "Security", "text": "Passwords must be rotated every 90 days and never shared."})
    hits = api.post(f"/api/knowledge/{kid}/search", json={"query": "how long do refunds take"}).json()["results"]
    assert hits and hits[0]["title"] == "Refunds"
    assert api.post("/api/knowledge", json={"name": "handbook"}).status_code == 409
    assert api.post(f"/api/knowledge/{kid}/documents", json={"title": "x", "text": "y" * 400_000}).status_code == 422
    agent = N("a", "llm", 0, 0, "a", provider="mock", model="mock-agent-1", tools=["knowledge.search"], max_iterations=3,
              prompt='Answer: {{ input.q }} [[tool:knowledge.search {"kb": "handbook", "query": "{{ input.q }}"}]]')
    _, r = run(api, mk(api, [START, agent, END], [E("start", "a"), E("a", "end")]), {"q": "refunds within days"})
    text = next(s for s in r["steps"] if s["node_id"] == "a")["output"]["text"]
    assert r["status"] == "succeeded" and "14 days" in text and "knowledge://" in text   # cited
    assert api.delete(f"/api/knowledge/{kid}").status_code == 200


class _Hook(http.server.BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        _Hook.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_connector_requires_approval_missing_creds_and_posts(api, monkeypatch):
    from backend import config
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "HTTP_ALLOW_PRIVATE_HOSTS", {"127.0.0.1"})
    node = N("s", "tool", 0, 0, "s", tool="slack.post_message", args={"text": "deploy {{ input.v }} done"})
    wid = mk(api, [START, node, END], [E("start", "s"), E("s", "end")])
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    rid, r = run(api, wid, {"v": "1.2"}, until={"waiting_for_approval"})
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 15)
    r = api.get(f"/api/runs/{rid}").json()
    assert r["status"] == "failed" and "SLACK_WEBHOOK_URL" in r["error"]["message"] and not _Hook.got   # clear setup message, nothing sent
    monkeypatch.setenv("SLACK_WEBHOOK_URL", f"http://127.0.0.1:{srv.server_port}:8080/x".replace(f":{srv.server_port}:8080", f":{srv.server_port}"))
    # the SSRF guard only permits ports 80/443/8080/8443 unless the host is explicitly allow-listed (it is, above)
    rid, r = run(api, wid, {"v": "1.2"}, until={"waiting_for_approval"})
    assert not _Hook.got                                   # approval gate: nothing posted yet
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 15)
    assert api.get(f"/api/runs/{rid}").json()["status"] == "succeeded", api.get(f"/api/runs/{rid}").json()["error"]
    assert _Hook.got == [{"text": "deploy 1.2 done"}]
    assert "127.0.0.1" not in json.dumps(api.get(f"/api/runs/{rid}").json()["tool_calls"])   # the webhook URL secret never lands in the trace
    srv.shutdown()


def test_every_template_validates_and_runs(api):
    from backend.workflows.examples import EXAMPLES
    from tests.test_lab import load
    for key, ex in EXAMPLES.items():
        wid, vid, inp = load(api, key)
        assert [i for i in api.get(f"/api/workflows/{wid}").json()["issues"] if i["level"] == "error"] == [], key
        rid, r = run(api, wid, inp, until={"succeeded", "failed", "waiting_for_approval", "cancelled", "timed_out"})
        assert r["status"] in ("succeeded", "waiting_for_approval") or key == "resilient", (key, r["status"], r.get("error"))
    assert any(m["category"] for m in api.get("/api/meta").json()["examples"])


def test_audit_log_records_admin_actions_without_bodies(api):
    before = api.get("/api/audit").json()["total"]
    wid = mk(api, [START, END], [E("start", "end")], name="audited")
    api.post(f"/api/workflows/{wid}/run", json={"input": {"api_key": "sk-secretsecretsecretsecret1"}})
    api.raw.post("/api/workflows", json={"name": "unauthenticated"})        # 401: not audited
    a = api.get("/api/audit").json()
    assert a["total"] >= before + 2
    paths = [e["path"] for e in a["entries"]]
    assert f"/api/workflows/{wid}/run" in paths
    assert "secretsecret" not in str(a)
    assert all(e["actor"] == "local-admin" for e in a["entries"])
