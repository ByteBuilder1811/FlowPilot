from backend.execution.worker import wait_for_run
from backend.workflows.examples import E, N
from backend.workflows.schema import validate_definition
from tests.helpers import END, START, mk, run
from tests.test_lab import load


def hook(api, wid, tok, body, **headers):
    return api.raw.post(f"/api/hooks/{wid}", json=body, headers={"X-FlowPilot-Webhook-Token": tok, **headers})


def webhook_wf(api, dedupe=None):
    start = N("start", "start", 0, 0, "s", trigger="webhook", **({"dedupe_key": dedupe} if dedupe else {}))
    m = N("m", "tool", 0, 0, "m", tool="mock.charge_card", requires_approval=False, args={"amount": 1, "customer": "{{ input.id }}"})
    wid = mk(api, [start, m, END], [E("start", "m"), E("m", "end")])
    tok = api.post(f"/api/workflows/{wid}/webhook-token").json()["token"]
    return wid, tok


def test_webhook_dedupe_by_payload_key_and_header(api):
    wid, tok = webhook_wf(api, "{{ input.event_id }}")
    a = hook(api, wid, tok, {"event_id": "evt1", "id": "c1"}).json()
    b = hook(api, wid, tok, {"event_id": "evt1", "id": "c1"}).json()   # provider re-delivery while run is in flight / waiting
    assert a["duplicate"] is False and b["duplicate"] is True and b["run_id"] == a["run_id"]
    c = hook(api, wid, tok, {"event_id": "evt2", "id": "c2"}).json()
    assert c["run_id"] != a["run_id"]
    h1 = hook(api, wid, tok, {"id": "c3"}, **{"Idempotency-Key": "k-1"}).json()
    h2 = hook(api, wid, tok, {"id": "c3"}, **{"Idempotency-Key": "k-1"}).json()
    assert h2["duplicate"] is True and h2["run_id"] == h1["run_id"]


def test_failed_delivery_can_be_reattempted_seen_is_not_done(api):
    wid, tok = webhook_wf(api, "{{ input.event_id }}")
    a = hook(api, wid, tok, {"event_id": "e9", "id": ""}).json()   # empty customer -> invalid_arguments -> failed
    wait_for_run(a["run_id"], 10)
    assert api.get(f"/api/runs/{a['run_id']}").json()["status"] == "failed"
    b = hook(api, wid, tok, {"event_id": "e9", "id": "now-valid"}).json()
    assert b["duplicate"] is False and b["run_id"] != a["run_id"] and b["retry_of"] == a["run_id"]


def test_safety_linter_flags_duplicate_risks():
    start = N("start", "start", 0, 0, "s", trigger="webhook")
    risky = N("m", "tool", 0, 0, "m", tool="mock.charge_card", args={"amount": 1, "customer": "x"}, retry={"max_attempts": 3, "safe_to_retry": True})
    d = {"nodes": [start, risky, END], "edges": [E("start", "m"), E("m", "end")]}
    msgs = " | ".join(i["message"] for i in validate_definition(d))
    assert "safe_to_retry" in msgs and "no event dedupe" in msgs
    safe = N("m", "tool", 0, 0, "m", tool="mock.send_email", args={"to": "a", "subject": "b"}, idempotency_key="{{ run.id }}", retry={"max_attempts": 3})
    d2 = {"nodes": [N("start", "start", 0, 0, "s", trigger="manual"), safe, END], "edges": [E("start", "m"), E("m", "end")]}
    assert not [i for i in validate_definition(d2) if "Duplicate" in i["message"] or "idempotency" in i["message"]]


def test_effects_ledger_tells_the_truth(api):
    args = {"to": "a@b.test", "subject": "ledger", "simulate": "unknown_outcome"}
    wid = mk(api, [START, N("m", "tool", 0, 0, "m", tool="mock.send_email", args=args), END], [E("start", "m"), E("m", "end")])
    rid, r = run(api, wid, until={"waiting_for_approval"})
    led = api.get(f"/api/runs/{rid}/effects").json()
    assert led["entries"] == [] and led["counts"]["executed"] == 0           # nothing happened before approval
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 15)
    led = api.get(f"/api/runs/{rid}/effects").json()
    assert led["counts"]["unknown"] == 1 and led["needs_attention"] is True and led["unapproved"] == 0
    rec = next(a for a in api.get(f"/api/runs/{rid}").json()["approvals"] if a["kind"] == "reconcile")
    api.post(f"/api/approvals/{rec['id']}/decide", json={"decision": "approve", "action": "mark_done", "output": {"ok": 1}})
    wait_for_run(rid, 15)
    assert api.get(f"/api/runs/{rid}/effects").json()["counts"]["reconciled"] == 1


def test_replay_ledger_is_simulated_not_executed(api):
    wid, v1, inp = load(api, "webhook")
    rid, r = run(api, wid, {"event_type": "order.created", "amount": 9000, "customer": "q"}, until={"waiting_for_approval"})
    api.post(f"/api/approvals/{r['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 15)
    case = api.post("/api/lab/cases", json={"run_id": rid}).json()["id"]
    res = api.post(f"/api/lab/cases/{case}/replay", json={}).json()
    led = api.get(f"/api/runs/{res['run_id']}/effects").json()
    assert led["counts"]["executed"] == 0 and led["counts"]["simulated"] == 1


def test_determinism_check(api):
    wid, v1, inp = load(api)
    rid, _ = run(api, wid, inp)
    case = api.post("/api/lab/cases", json={"run_id": rid}).json()["id"]
    d = api.post(f"/api/lab/cases/{case}/determinism", json={"runs": 4}).json()
    assert d["identical"] is True and d["distinct_fingerprints"] == 1 and len(d["run_ids"]) == 4
    assert api.post(f"/api/lab/cases/{case}/determinism", json={"runs": 99}).status_code == 422


def test_version_matrix_pinpoints_the_breaking_version(api):
    wid, v1, inp = load(api)
    d = api.get(f"/api/workflows/{wid}").json()["draft"]
    pol = next(n for n in d["nodes"] if n["id"] == "policy")["data"]["config"]
    pol["max_attempts"] = 3
    api.put(f"/api/workflows/{wid}", json={"definition": d})
    v2 = api.post(f"/api/workflows/{wid}/versions", json={"note": "good: 3 attempts"}).json()
    rid, _ = run(api, wid, inp, until={"waiting_for_approval"})
    api.post(f"/api/approvals/{api.get(f'/api/runs/{rid}').json()['approvals'][0]['id']}/decide", json={"decision": "approve"})
    wait_for_run(rid, 15)
    case = api.post("/api/lab/cases", json={"run_id": rid, "name": "happy path"}).json()["id"]
    pol["max_attempts"] = 1                                   # v3 reintroduces the bug
    api.put(f"/api/workflows/{wid}", json={"definition": d})
    v3 = api.post(f"/api/workflows/{wid}/versions", json={"note": "oops: back to 1 attempt"}).json()
    m = api.post("/api/lab/matrix", json={"workflow_id": wid, "case_ids": [case]}).json()
    row = m["cases"][0]
    statuses = [row["cells"][v["id"]]["status"] for v in m["versions"]]
    assert statuses == ["passed", "passed", "failed"] or statuses[-1] == "failed"
    assert row["regressed_in"]["version_no"] == 3
    assert row["regressed_in"]["diff"]["nodes_changed"][0]["fields"][0]["a"] == 3
