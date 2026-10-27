import time

import pytest

from backend.execution import state
from backend.workflows.examples import E, N
from backend.workflows.schema import validate_definition
from tests.helpers import END, START, mk, run


def test_state_machine_terminal_states_are_final():
    for t in state.TERMINAL:
        for to in ("running", "queued", "retrying", "waiting_for_approval"):
            with pytest.raises(state.IllegalTransition):
                state.check(t, to)
    state.check("queued", "running")
    with pytest.raises(state.IllegalTransition):
        state.check("queued", "succeeded")


def test_validation_catches_bad_workflows():
    assert validate_definition("nope")[0]["level"] == "error"
    d = {"nodes": [N("start", "start", 0, 0, "s"), N("a", "condition", 0, 0, "c", expression="__import__('os')"), N("end", "end", 0, 0, "e")],
         "edges": [E("start", "a"), E("a", "end", "true"), E("a", "ghost", "false")]}
    msgs = " | ".join(i["message"] for i in validate_definition(d) if i["level"] == "error")
    assert "does not exist" in msgs and "Invalid expression" in msgs
    cyc = {"nodes": [N("start", "start", 0, 0, "s"), N("t", "transform", 0, 0, "t", op="template", template="x"), N("end", "end", 0, 0, "e")],
           "edges": [E("start", "t"), E("t", "t"), E("t", "end", "error")]}
    assert any("unbounded loop" in i["message"] for i in validate_definition(cyc))
    lp = {"nodes": [START, N("l", "loop", 0, 0, "l", mode="count", count=3), END], "edges": [E("start", "l"), E("l", "end", "done")]}
    assert any("max_iterations" in i["message"] for i in validate_definition(lp))


def test_expression_sandbox_blocks_escape():
    from backend.workflows.expr import ExprError, evaluate
    for bad in ["__import__('os').system('x')", "().__class__", "open('/etc/passwd')", "[x for x in range(9)]", "lambda: 1", "input.__dict__"]:
        with pytest.raises(ExprError):
            evaluate(bad, {"input": {}})
    assert evaluate("input.a + 2 > 3 and 'x' in input.s", {"input": {"a": 2, "s": "xyz"}}) is True


def test_successful_run_persists_steps_events_and_survives_reload(api):
    wid = mk(api, [START, N("t", "transform", 0, 0, "t", op="template", template="hi {{ input.name }}"), END],
             [E("start", "t"), E("t", "end")])
    rid, r = run(api, wid, {"name": "ada"})
    assert r["status"] == "succeeded" and r["output"]["result"]["t"]["value"] == "hi ada"
    assert [s["status"] for s in r["steps"]] == ["succeeded"] * 3
    evs = api.get(f"/api/runs/{rid}/events").json()["events"]
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    types = [e["type"] for e in evs]
    assert types[0] == "run.queued" and "run.succeeded" in types
    # reconnect: only missed events are returned
    mid = seqs[3]
    assert [e["seq"] for e in api.get(f"/api/runs/{rid}/events", params={"after": mid}).json()["events"]] == [s for s in seqs if s > mid]
    assert api.get(f"/api/runs/{rid}").json()["id"] == rid


def test_transient_failure_retried_within_bound(api):
    nodes = [START, N("rp", "retry", 0, 0, "p", max_attempts=3, backoff_ms=10, max_backoff_ms=20),
             N("f", "tool", 0, 0, "f", tool="mock.flaky_lookup", args={"key": "k", "fail_times": 2}), END]
    wid = mk(api, nodes, [E("start", "rp"), E("rp", "f"), E("f", "end")])
    _, r = run(api, wid)
    assert r["status"] == "succeeded"
    step = next(s for s in r["steps"] if s["node_id"] == "f")
    assert len(step["attempts"]) == 2 and all(a["decision"] == "retry" for a in step["attempts"])
    assert r["usage"]["retries"] == 2


def test_retry_budget_exhausted_fails_with_reason(api):
    nodes = [START, N("f", "tool", 0, 0, "f", tool="mock.flaky_lookup", args={"key": "k", "fail_times": 5}, retry={"max_attempts": 2, "backoff_ms": 5}), END]
    wid = mk(api, nodes, [E("start", "f"), E("f", "end")])
    _, r = run(api, wid)
    assert r["status"] == "failed" and r["error"]["code"] == "tool_unavailable"
    assert "exhausted" in r["error"]["details"]["retry_reason"]
    assert len([c for c in r["tool_calls"] if c["tool"] == "mock.flaky_lookup"]) == 2


def test_permanent_error_not_retried(api):
    nodes = [START, N("f", "tool", 0, 0, "f", tool="mock.search", args={}, retry={"max_attempts": 4}), END]
    wid = mk(api, nodes, [E("start", "f"), E("f", "end")])
    _, r = run(api, wid)
    assert r["status"] == "failed" and r["error"]["code"] == "invalid_arguments"
    assert r["usage"]["retries"] == 0


def test_error_edge_provides_recovery_path(api):
    nodes = [START, N("f", "tool", 0, 0, "f", tool="mock.flaky_lookup", args={"key": "k", "fail_times": 9}),
             N("fb", "set_variable", 0, 0, "fb", name="mode", value="degraded"), N("end", "end", 0, 0, "e", output="{{ vars.mode }}")]
    wid = mk(api, nodes, [E("start", "f"), E("f", "end"), E("f", "fb", "error"), E("fb", "end")])
    _, r = run(api, wid)
    assert r["status"] == "succeeded" and r["output"]["result"] == "degraded"


def test_unknown_outcome_side_effect_is_not_blindly_retried(api):
    args = {"to": "a@b.test", "subject": "s", "simulate": "unknown_outcome"}
    nodes = [START, N("m", "tool", 0, 0, "m", tool="mock.send_email", args=args, retry={"max_attempts": 3, "backoff_ms": 5}), END]
    wid = mk(api, nodes, [E("start", "m"), E("m", "end")])
    rid, r = run(api, wid, until={"waiting_for_approval", "succeeded", "failed"})
    assert r["status"] == "waiting_for_approval"          # approval gate first
    gate = next(a for a in r["approvals"] if a["kind"] == "gate")
    api.post(f"/api/approvals/{gate['id']}/decide", json={"decision": "approve"})
    from backend.execution.worker import wait_for_run
    wait_for_run(rid, 20, until={"waiting_for_approval", "succeeded", "failed"})
    r = api.get(f"/api/runs/{rid}").json()
    assert r["status"] == "waiting_for_approval"
    rec = next(a for a in r["approvals"] if a["kind"] == "reconcile" and a["status"] == "pending")
    assert len(api.get("/api/mock/outbox").json()) >= 1
    sent_before = [o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid]
    assert len(sent_before) == 1                          # exactly one send so far, no automatic repeat
    assert any(e["type"] == "retry.blocked" for e in api.get(f"/api/runs/{rid}/events").json()["events"])
    # operator says "it did happen": no second email is ever sent
    api.post(f"/api/approvals/{rec['id']}/decide", json={"decision": "approve", "action": "mark_done", "output": {"message_id": "manual"}})
    wait_for_run(rid, 20)
    r = api.get(f"/api/runs/{rid}").json()
    assert r["status"] == "succeeded"
    assert len([o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid]) == 1


def test_idempotency_key_makes_retry_safe_and_deduplicates(api):
    args = {"to": "a@b.test", "subject": "s", "simulate": "unknown_outcome"}
    nodes = [START, N("m", "tool", 0, 0, "m", tool="mock.send_email", args=args, idempotency_key="{{ run.id }}-k", requires_approval=True,
                      retry={"max_attempts": 3, "backoff_ms": 5}), END]
    wid = mk(api, nodes, [E("start", "m"), E("m", "end")])
    rid, r = run(api, wid, until={"waiting_for_approval"})
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    from backend.execution.worker import wait_for_run
    wait_for_run(rid, 20)
    r = api.get(f"/api/runs/{rid}").json()
    assert r["status"] == "succeeded"
    assert len([o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid]) == 1
    assert [c["outcome"] for c in r["tool_calls"] if c["tool"] == "mock.send_email"] == ["unknown", "ok"]


def _approval_flow(api, decision):
    nodes = [START, N("m", "tool", 0, 0, "m", tool="mock.charge_card", args={"amount": 5, "customer": "c"}), END]
    wid = mk(api, nodes, [E("start", "m"), E("m", "end")])
    rid, r = run(api, wid, until={"waiting_for_approval"})
    assert r["status"] == "waiting_for_approval"
    assert [o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid] == []   # nothing happened before the decision
    ap = r["approvals"][0]
    assert ap["proposed"]["tool"] == "mock.charge_card" and ap["proposed"]["arguments"]["amount"] == 5
    api.post(f"/api/approvals/{ap['id']}/decide", json={"decision": decision})
    from backend.execution.worker import wait_for_run
    wait_for_run(rid, 20)
    return rid, api.get(f"/api/runs/{rid}").json()


def test_approval_reject_blocks_side_effect(api):
    rid, r = _approval_flow(api, "reject")
    assert r["status"] == "failed" and r["error"]["code"] == "approval_rejected"
    assert [o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid] == []


def test_approval_approve_executes_once_with_approval_id(api):
    rid, r = _approval_flow(api, "approve")
    assert r["status"] == "succeeded"
    out = [o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid]
    assert len(out) == 1 and out[0]["approval_id"] == r["approvals"][0]["id"]


def test_approval_cannot_be_decided_twice_or_without_token(api):
    rid, r = _approval_flow(api, "approve")
    aid = r["approvals"][0]["id"]
    assert api.post(f"/api/approvals/{aid}/decide", json={"decision": "reject"}).status_code == 409
    assert api.raw.post(f"/api/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 401


def test_approval_bound_to_exact_arguments():
    from backend.execution.runner import verify_approved
    assert verify_approved(None, "r", "h") is False
    assert verify_approved("apr_missing", "r", "h") is False


def test_cancel_running_run(api):
    nodes = [START, N("d", "delay", 0, 0, "d", seconds=30), END]
    wid = mk(api, nodes, [E("start", "d"), E("d", "end")])
    rid = api.post(f"/api/workflows/{wid}/run", json={}).json()["run_id"]
    from backend.execution.worker import wait_for_run
    deadline = time.time() + 5
    while time.time() < deadline and api.get(f"/api/runs/{rid}").json()["current_node"] != "d":
        time.sleep(0.05)
    assert api.post(f"/api/runs/{rid}/cancel").status_code == 200
    assert wait_for_run(rid, 10) == "cancelled"
    assert api.post(f"/api/runs/{rid}/cancel").status_code == 409


def test_loop_strict_limit_and_foreach(api):
    body = N("b", "set_variable", 0, 0, "b", name="last", value="{{ loop.l.i }}")
    mkl = lambda cfg: mk(api, [START, N("l", "loop", 0, 0, "l", **cfg), body, END], [E("start", "l"), E("l", "b", "body"), E("b", "l"), E("l", "end", "done")])  # noqa: E731
    _, r = run(api, mkl({"mode": "count", "count": 3, "max_iterations": 5}))
    assert r["status"] == "succeeded" and sum(1 for s in r["steps"] if s["node_id"] == "b") == 3
    _, r = run(api, mkl({"mode": "count", "count": 50, "max_iterations": 5}))
    assert r["status"] == "failed" and r["error"]["code"] == "loop_limit_exceeded"
    _, r = run(api, mkl({"mode": "foreach", "items": "input.xs", "max_iterations": 2}), {"xs": [1, 2, 3]})
    assert r["error"]["code"] == "loop_limit_exceeded"


def test_max_steps_budget(api):
    body = N("b", "set_variable", 0, 0, "b", name="x", value=1)
    wid = mk(api, [START, N("l", "loop", 0, 0, "l", mode="count", count=50, max_iterations=50), body, END],
             [E("start", "l"), E("l", "b", "body"), E("b", "l"), E("l", "end", "done")], policy={"max_steps": 10})
    _, r = run(api, wid)
    assert r["status"] == "failed" and r["error"]["code"] == "max_steps_exceeded"


def test_agent_loop_detection_and_iteration_budget(api):
    llm = N("a", "llm", 0, 0, "a", provider="mock", model="mock-agent-1", tools=["mock.search"], max_iterations=10,
            prompt='go [[tool:mock.search {"query": "same"}]] [[loop]]')
    wid = mk(api, [START, llm, END], [E("start", "a"), E("a", "end")])
    _, r = run(api, wid)
    assert r["status"] == "failed" and r["error"]["code"] == "loop_detected"
    assert r["error"]["details"]["signal"] == "repeated_tool_call"
    llm2 = {**llm, "data": {"label": "a", "config": {**llm["data"]["config"], "max_iterations": 2}}}
    wid = mk(api, [START, llm2, END], [E("start", "a"), E("a", "end")])
    _, r = run(api, wid)
    assert r["error"]["code"] in ("agent_iteration_limit", "loop_detected")


def test_mock_llm_is_labelled_mock(api):
    llm = N("a", "llm", 0, 0, "a", provider="mock", model="mock-agent-1", prompt="hello")
    _, r = run(api, mk(api, [START, llm, END], [E("start", "a"), E("a", "end")]))
    out = next(s for s in r["steps"] if s["node_id"] == "a")["output"]
    assert out["mock"] is True and out["text"].startswith("[mock-llm") and r["usage"]["input_tokens"] is None and r["usage"]["cost_usd"] is None


def test_missing_provider_credentials_is_a_clean_failure(api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = N("a", "llm", 0, 0, "a", provider="anthropic", model="claude-haiku-5-5", prompt="hello")
    _, r = run(api, mk(api, [START, llm, END], [E("start", "a"), E("a", "end")]))
    assert r["status"] == "failed" and "ANTHROPIC_API_KEY" in r["error"]["message"]


def test_secrets_are_redacted_everywhere(api):
    secret = "sk-ant-ABCDEFGHIJKLMNOPQRSTUV123456"
    nodes = [START, N("t", "transform", 0, 0, "t", op="template", template="key is " + secret), END]
    wid = mk(api, nodes, [E("start", "t"), E("t", "end")])
    rid, r = run(api, wid, {"api_key": "hunter2hunter2", "note": "Bearer abcdefghijklmnop"})
    blob = str(r) + str(api.get(f"/api/runs/{rid}/events").json())
    assert secret not in blob and "hunter2" not in blob and "abcdefghijklmnop" not in blob
    assert "[REDACTED]" in blob


def test_workflow_versions_are_immutable(api):
    wid = mk(api, [START, N("t", "transform", 0, 0, "t", op="template", template="v1"), END], [E("start", "t"), E("t", "end")])
    rid1, r1 = run(api, wid)
    v1 = r1["version_id"]
    wf = api.get(f"/api/workflows/{wid}").json()
    d = wf["draft"]
    d["nodes"][1]["data"]["config"]["template"] = "v2"
    api.put(f"/api/workflows/{wid}", json={"definition": d})
    rid2, r2 = run(api, wid)
    assert r2["version_id"] != v1 and r2["version_no"] == 2
    assert api.get(f"/api/workflows/{wid}/versions/{v1}").json()["definition"]["nodes"][1]["data"]["config"]["template"] == "v1"
    assert api.get(f"/api/runs/{rid1}").json()["output"]["result"]["t"]["value"] == "v1"
    # same content -> no new version
    _, r3 = run(api, wid)
    assert r3["version_id"] == r2["version_id"]


def test_invalid_workflow_cannot_run(api):
    wid = mk(api, [START, N("t", "transform", 0, 0, "t", op="expression"), END], [E("start", "t"), E("t", "end")])
    r = api.post(f"/api/workflows/{wid}/run", json={})
    assert r.status_code == 400 and r.json()["detail"]["issues"]


def test_malicious_and_oversized_input_rejected(api):
    assert api.post("/api/workflows", json={"name": "x", "definition": {"nodes": "no", "edges": []}}).status_code == 200  # drafts may be invalid
    assert api.post("/api/workflows/import", json={"format": "other"}).status_code == 400
    assert api.post("/api/workflows/import", json={"format": "flowpilot.workflow/v1", "definition": {"nodes": [{"id": "x", "type": "exec"}], "edges": []}}).status_code == 400
    wid = mk(api, [START, END], [E("start", "end")])
    assert api.post(f"/api/workflows/{wid}/run", json={"input": {"x": "a" * 300_000}}).status_code == 413
    assert api.post("/api/workflows", json={"name": "", "definition": None}).status_code == 422


def test_protected_operations_require_token(api):
    for method, path in [("post", "/api/workflows"), ("delete", "/api/workflows/x"), ("put", "/api/settings"), ("post", "/api/runs/x/cancel"), ("post", "/api/lab/cases")]:
        assert getattr(api.raw, method)(path, **({"json": {}} if method != "delete" else {})).status_code == 401
    assert api.raw.post("/api/workflows", headers={"X-FlowPilot-Token": "wrong"}, json={"name": "a"}).status_code == 401


def test_host_header_and_cors_guard(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/session", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/session").status_code == 200


def test_webhook_auth_and_validation(api):
    start = N("start", "start", 0, 0, "s", trigger="webhook", input_schema={"required": ["n"], "properties": {"n": {"type": "number"}}})
    wid = mk(api, [start, END], [E("start", "end")])
    assert api.raw.post(f"/api/hooks/{wid}", json={"n": 1}).status_code == 401
    tok = api.post(f"/api/workflows/{wid}/webhook-token").json()["token"]
    assert api.raw.post(f"/api/hooks/{wid}", json={"n": 1}, headers={"X-FlowPilot-Webhook-Token": "bad"}).status_code == 401
    ok = api.raw.post(f"/api/hooks/{wid}", json={"n": 1}, headers={"X-FlowPilot-Webhook-Token": tok})
    assert ok.status_code == 200
    from backend.execution.worker import wait_for_run
    assert wait_for_run(ok.json()["run_id"], 10) == "succeeded"
    bad = api.raw.post(f"/api/hooks/{wid}", json={"n": "x"}, headers={"X-FlowPilot-Webhook-Token": tok})
    from backend.execution.worker import wait_for_run as w2
    assert w2(bad.json()["run_id"], 10) == "failed"
    assert api.raw.post(f"/api/hooks/{wid}", content=b"{" , headers={"X-FlowPilot-Webhook-Token": tok}).status_code == 400
    assert api.raw.post(f"/api/hooks/{wid}", content=b"x" * 300_000, headers={"X-FlowPilot-Webhook-Token": tok}).status_code == 413


def test_ssrf_guard():
    from backend.security.ssrf import SSRFBlocked, validate_url
    for u in ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd",
              "http://user:pw@example.com/", "http://metadata.google.internal/", "http://192.168.1.1/", "http://example.com:22/", "http://0.0.0.0/", "http://2130706433/"]:
        with pytest.raises(SSRFBlocked):
            validate_url(u)


def test_http_node_blocks_private_target_end_to_end(api):
    nodes = [START, N("h", "http", 0, 0, "h", url="http://169.254.169.254/latest/meta-data", method="GET"), END]
    _, r = run(api, mk(api, nodes, [E("start", "h"), E("h", "end")]))
    assert r["status"] == "failed" and r["error"]["code"] == "tool_blocked"
