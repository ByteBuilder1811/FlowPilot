from backend.db.models import Run, ToolCall
from backend.db.session import new_id, now, session
from backend.execution import store
from backend.execution.worker import recover_orphans, wait_for_run
from backend.workflows.examples import EXAMPLES
from tests.helpers import run


def load(api, key="resilient"):
    j = api.post(f"/api/examples/{key}/load").json()
    return j["id"], j["version_id"], j["sample_input"]


def test_examples_are_executable(api):
    wid, _, inp = load(api, "research")
    _, r = run(api, wid, inp)
    assert r["status"] == "succeeded" and "Sources:" in r["output"]["result"] and "https://example.org/sample" in r["output"]["result"]
    wid, _, inp = load(api, "webhook")
    _, r = run(api, wid, {"event_type": "order.created", "amount": 10, "customer": "x"})
    assert r["status"] == "succeeded" and r["output"]["result"]["routed"] == "normal"
    _, r = run(api, wid, {"event_type": "nope", "amount": 10})
    assert r["output"]["result"]["routed"] == "ignored"


def test_failure_to_regression_to_comparison(api):
    wid, v1, inp = load(api)
    rid, r = run(api, wid, inp)
    assert r["status"] == "failed" and r["error"]["code"] == "tool_unavailable"
    case = api.post("/api/lab/cases", json={"run_id": rid, "name": "flaky lookup"}).json()
    # replay v1 with fixtures: deterministic, still failing
    a = api.post(f"/api/lab/cases/{case['id']}/replay", json={}).json()
    b = api.post(f"/api/lab/cases/{case['id']}/replay", json={}).json()
    assert a["status"] == b["status"] == "failed" and a["metrics"]["steps"] == b["metrics"]["steps"]
    rr = api.get(f"/api/runs/{a['run_id']}").json()
    assert rr["mode"] == "fixture_replay" and all(c["source"] in ("fixture", "deterministic", "fault", "simulated") for c in rr["tool_calls"])
    # fix: raise retries, new version
    d = api.get(f"/api/workflows/{wid}").json()["draft"]
    next(n for n in d["nodes"] if n["id"] == "policy")["data"]["config"]["max_attempts"] = 3
    api.put(f"/api/workflows/{wid}", json={"definition": d})
    v2 = api.post(f"/api/workflows/{wid}/versions", json={"note": "retries=3"}).json()
    cmp_ = api.post("/api/lab/compare", json={"workflow_id": wid, "version_a": v1, "version_b": v2["id"]}).json()
    assert cmp_["summary"]["improvement"] == 1 and  cmp_["summary"]["regression"] == 0 and cmp_["summary"]["sample_size"] == 1
    assert cmp_["structural"]["nodes_changed"][0]["node_id"] == "policy"
    assert cmp_["cases"][0]["b"]["status"] == "passed"
    # reverse direction is a regression
    rev = api.post("/api/lab/compare", json={"workflow_id": wid, "version_a": v2["id"], "version_b": v1}).json()
    assert rev["summary"]["regression"] == 1
    assert no_real_email(api, a["run_id"])


def no_real_email(api, run_id):
    return all(o["run_id"] != run_id for o in api.get("/api/mock/outbox").json())


def test_fault_injection_and_export_import(api):
    wid, v1, inp = load(api)
    d = api.get(f"/api/workflows/{wid}").json()["draft"]
    next(n for n in d["nodes"] if n["id"] == "policy")["data"]["config"]["max_attempts"] = 3
    api.put(f"/api/workflows/{wid}", json={"definition": d})
    rid, r = run(api, wid, inp, until={"waiting_for_approval"})
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 20)
    assert api.get(f"/api/runs/{rid}").json()["status"] == "succeeded"
    case = api.post("/api/lab/cases", json={"run_id": rid, "name": "happy"}).json()["id"]
    assert api.post(f"/api/lab/cases/{case}/replay", json={}).json()["status"] == "passed"
    # inject 5 timeouts on the lookup: retry budget (3) is exhausted -> case fails
    res = api.post(f"/api/lab/cases/{case}/replay", json={"faults": [{"tool": "mock.flaky_lookup", "kind": "rate_limit", "times": 5}]}).json()
    assert res["status"] == "failed"
    assert api.post(f"/api/lab/cases/{case}/replay", json={"faults": [{"kind": "bogus"}]}).status_code == 422
    exp = api.get(f"/api/lab/cases/{case}/export").json()
    imp = api.post("/api/lab/cases/import", json={"workflow_id": wid, "data": exp})
    assert imp.status_code == 200
    assert api.post(f"/api/lab/cases/{imp.json()['id']}/replay", json={}).json()["status"] == "passed"
    assert api.post("/api/lab/cases/import", json={"workflow_id": wid, "data": {"format": "x"}}).status_code == 400


def test_fixture_miss_is_inconclusive_not_a_fake_pass(api):
    from backend.workflows.examples import E, N
    from tests.helpers import END, START, mk
    wid = mk(api, [START, N("h", "http", 0, 0, "h", url="https://example.org/", method="GET"), END], [E("start", "h"), E("h", "end")])
    rid, r = run(api, wid)          # live: real HTTP not needed to create the case; run result may be fail/succeed offline
    case = api.post("/api/lab/cases", json={"run_id": rid}).json()["id"]
    from backend.db.models import RegressionCase
    with session() as s:
        s.get(RegressionCase, case).fixtures = []
    res = api.post(f"/api/lab/cases/{case}/replay", json={}).json()
    assert res["status"] == "inconclusive" and res["metrics"]["error"]["code"] == "fixture_miss"


def test_replay_never_executes_side_effects(api):
    wid, v1, inp = load(api, "webhook")
    rid, r = run(api, wid, {"event_type": "order.created", "amount": 5000, "customer": "z"}, until={"waiting_for_approval"})
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 20)
    case = api.post("/api/lab/cases", json={"run_id": rid}).json()["id"]
    res = api.post(f"/api/lab/cases/{case}/replay", json={}).json()
    assert res["status"] == "passed"
    assert [o for o in api.get("/api/mock/outbox").json() if o["run_id"] == res["run_id"]] == []


def test_restart_recovery_never_repeats_inflight_side_effect(api):
    """Simulate a crash after the side effect request was recorded as 'started' but before its result was saved."""
    from backend.workflows.examples import E, N
    from tests.helpers import END, START, mk
    nodes = [START, N("m", "tool", 0, 0, "m", tool="mock.charge_card", args={"amount": 9, "customer": "c"}), END]
    wid = mk(api, nodes, [E("start", "m"), E("m", "end")])
    vid = api.post(f"/api/workflows/{wid}/versions", json={}).json()["id"]
    from backend.db.models import ApprovalRequest, RunStep
    from backend.execution.runner import sha
    crash_id = new_id("apr")
    rid = new_id("run")                      # staged directly as 'running' under a dead worker: it is never queued, so no live worker can race the setup
    with session() as s:
        s.add(Run(id=rid, workflow_id=wid, version_id=vid, status="running", mode="live", trigger="manual", input={}, options={}, lease_owner="worker-that-died",
                  usage={"llm_calls": 0, "tool_calls": 0, "retries": 0}, created_at=now(), started_at=now()))
    store.emit(rid, "run.started", {"mode": "live"})
    # pre-existing state as if a previous process died mid-call: approval granted, charge row 'started'
    with session() as s:
        s.add(RunStep(id=new_id("stp"), run_id=rid, node_id="start", node_type="start", visit=0, status="succeeded", output={}, handle="out", attempts=[], started_at=now()))
        s.add(RunStep(id=new_id("stp"), run_id=rid, node_id="m", node_type="tool", visit=0, status="running", attempts=[], started_at=now()))
        h = sha({"tool": "mock.charge_card", "args": {"amount": 9, "customer": "c"}})
        s.add(ApprovalRequest(id=crash_id, run_id=rid, node_id="m", visit=0, kind="gate", status="approved", args_hash=h, proposed={}, created_at=now(), decided_at=now()))
        s.add(ToolCall(id=new_id("tc"), run_id=rid, node_id="m", kind="tool", tool="mock.charge_card", visit=0, seq=0, ordinal=0, attempt=1, args={}, args_hash=h,
                       outcome="started", side_effect=True, approval_id=crash_id, started_at=now()))
    assert recover_orphans() >= 1            # only runs whose owner is gone; live workers' runs are untouched
    st = wait_for_run(rid, 20)
    assert st == "waiting_for_approval", api.get(f"/api/runs/{rid}").json()["error"]
    r = api.get(f"/api/runs/{rid}").json()
    assert any(a["kind"] == "reconcile" and a["status"] == "pending" for a in r["approvals"])
    assert [o for o in api.get("/api/mock/outbox").json() if o["run_id"] == rid] == []   # the charge was NOT repeated
    assert any(e["type"] == "run.queued" or e["data"].get("reason") == "recovered after restart" for e in api.get(f"/api/runs/{rid}/events").json()["events"])


def test_recovery_leaves_runs_owned_by_live_workers_alone(api):
    """Regression for a real hazard: recovering 'all running runs' stole work from live workers."""
    from backend.workflows.examples import E, N
    from tests.helpers import END, START, mk
    wid = mk(api, [START, N("d", "delay", 0, 0, "d", seconds=3), END], [E("start", "d"), E("d", "end")])
    rid = api.post(f"/api/workflows/{wid}/run", json={}).json()["run_id"]
    import time
    for _ in range(100):
        if api.get(f"/api/runs/{rid}").json()["current_node"] == "d":
            break
        time.sleep(0.05)
    assert recover_orphans() == 0 or api.get(f"/api/runs/{rid}").json()["status"] == "running"
    assert wait_for_run(rid, 15) == "succeeded"
    ev = [e["type"] + ":" + str(e["data"].get("reason", "")) for e in api.get(f"/api/runs/{rid}/events").json()["events"]]
    assert not any("owner worker is gone" in x for x in ev)
