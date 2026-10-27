from backend.workflows.examples import E, N
from tests.helpers import END, START, mk


def _wf(api):
    t = N("t", "transform", 0, 0, "t", op="template", template="Hello {{ input.name }}, welcome aboard to FlowPilot")
    return mk(api, [START, t, N("end", "end", 0, 0, "e", output="{{ nodes.t.value }}")], [E("start", "t"), E("t", "end")])


def test_eval_scorers_and_dataset_lifecycle(api):
    wid = _wf(api)
    vid = api.post(f"/api/workflows/{wid}/versions", json={}).json()["id"]
    did = api.post("/api/evals/datasets", json={"name": "greetings", "workflow_id": wid}).json()["id"]
    api.post(f"/api/evals/datasets/{did}/rows", json={"rows": [
        {"input": {"name": "ada"}, "expected": {"contains": "Hello ada", "regex": "welcome\\s+aboard", "output": "Hello ada, welcome aboard to FlowPilot", "rubric": "greeting welcome FlowPilot"}},
        {"input": {"name": "bob"}, "expected": {"contains": "Goodbye bob", "output": "nope"}},
    ]})
    r = api.post("/api/evals/run", json={"dataset_id": did, "version_id": vid, "scorers": ["exact", "contains", "regex", "judge"], "judge": {"provider": "mock", "threshold": 0.6}}).json()
    assert r["summary"] == {"passed": 1, "failed": 1, "errored": 0, "blocked": 0, "rows": 2}
    full = api.get(f"/api/evals/runs/{r['id']}").json()
    good = next(x for x in full["results"] if x["input"]["name"] == "ada")
    assert good["status"] == "passed" and {s["scorer"] for s in good["scores"]} == {"exact", "contains", "regex", "judge"}
    judge = next(s for s in good["scores"] if s["scorer"] == "judge")
    assert judge["judge"] == "mock-heuristic" and "MOCK" in judge["detail"]            # never passes as a real judge
    bad = next(x for x in full["results"] if x["input"]["name"] == "bob")
    assert bad["status"] == "failed" and not any(s["passed"] for s in bad["scores"] if s["scorer"] in ("exact", "contains"))
    assert api.post("/api/evals/run", json={"dataset_id": did, "version_id": vid, "scorers": ["magic"]}).status_code == 400
    assert api.post("/api/evals/run", json={"dataset_id": did, "version_id": vid, "scorers": []}).status_code == 422
    assert api.post(f"/api/evals/datasets/{did}/rows", json={"rows": [{"input": "x"}]}).status_code == 422


def test_eval_never_bypasses_approval(api):
    node = N("m", "tool", 0, 0, "m", tool="mock.charge_card", args={"amount": 5, "customer": "{{ input.c }}"})
    wid = mk(api, [START, node, END], [E("start", "m"), E("m", "end")])
    vid = api.post(f"/api/workflows/{wid}/versions", json={}).json()["id"]
    did = api.post("/api/evals/datasets", json={"name": "pay", "workflow_id": wid}).json()["id"]
    api.post(f"/api/evals/datasets/{did}/rows", json={"rows": [{"input": {"c": "evalcustomer"}, "expected": {"contains": "x"}}]})
    r = api.post("/api/evals/run", json={"dataset_id": did, "version_id": vid, "scorers": ["contains"]}).json()
    assert r["summary"]["blocked"] == 1
    assert [o for o in api.get("/api/mock/outbox").json() if o["payload"].get("customer") == "evalcustomer"] == []


def test_insights_reflect_real_runs_only(api):
    wid = _wf(api)
    api.post(f"/api/workflows/{wid}/run", json={"input": {"name": "z"}})
    from backend.execution.worker import wait_for_run
    import time
    time.sleep(1.0)
    d = api.get("/api/insights?days=7").json()
    assert d["total_runs"] >= 1 and d["cost_usd"] is None and d["tokens"] is None
    assert d["duration_ms"]["samples"] >= 1 and any("Live runs only" in n for n in d["notes"])
    assert all(t["failure_rate"] <= 1 for t in d["tools"])
    assert api.get("/api/insights?days=999").status_code == 422
