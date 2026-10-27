import time

from backend.execution.worker import wait_for_run
from backend.workflows.examples import E, N
from tests.helpers import END, START, mk, run


def child_wf(api, name="child", tpl="child:{{ input.n }}"):
    return mk(api, [START, N("t", "transform", 0, 0, "t", op="template", template=tpl), N("end", "end", 0, 0, "e", output="{{ nodes.t.value }}")], [E("start", "t"), E("t", "end")], name=name)


def parent_wf(api, cfg, name="parent"):
    sub = N("s", "subworkflow", 0, 0, "s", **cfg)
    return mk(api, [START, sub, N("end", "end", 0, 0, "e", output="{{ nodes.s }}")], [E("start", "s"), E("s", "end")], name=name)


def test_single_subworkflow_returns_child_output(api):
    c = child_wf(api)
    _, r = run(api, parent_wf(api, {"workflow_id": c, "input": {"n": "{{ input.x }}"}}), {"x": 7})
    assert r["status"] == "succeeded" and r["output"]["result"]["result"] == "child:7"
    assert len(r["children"]) == 1 and r["children"][0]["status"] == "succeeded"
    kid = api.get(f"/api/runs/{r['children'][0]['id']}").json()
    assert kid["parent_run_id"] == r["id"] and kid["trigger"] == "subworkflow"


def test_map_fans_out_in_parallel_and_keeps_order(api):
    slow = mk(api, [START, N("d", "delay", 0, 0, "d", seconds=1), N("t", "transform", 0, 0, "t", op="template", template="item-{{ input.n }}"), N("end", "end", 0, 0, "e", output="{{ nodes.t.value }}")],
              [E("start", "d"), E("d", "t"), E("t", "end")], name="slow")
    p = parent_wf(api, {"workflow_id": slow, "mode": "foreach", "items": "input.ns", "max_items": 6, "input": {"n": "{{ item }}"}})
    t0 = time.time()
    _, r = run(api, p, {"ns": [1, 2, 3]}, timeout=30)
    took = time.time() - t0
    assert r["status"] == "succeeded", r.get("error")
    assert [x["result"] for x in r["output"]["result"]["results"]] == ["item-1", "item-2", "item-3"]
    assert r["output"]["result"]["succeeded"] == 3
    kids = [api.get(f"/api/runs/{k['id']}").json() for k in r["children"]]
    starts = [k["started_at"] for k in kids]; ends = [k["completed_at"] for k in kids]
    assert max(starts) < min(ends)                                        # every child had started before any finished: truly concurrent


def test_child_failure_propagates_or_is_recoverable(api):
    bad = mk(api, [START, N("f", "tool", 0, 0, "f", tool="mock.flaky_lookup", args={"key": "k", "fail_times": 9}), END], [E("start", "f"), E("f", "end")], name="bad")
    _, r = run(api, parent_wf(api, {"workflow_id": bad}))
    assert r["status"] == "failed" and r["error"]["code"] == "subworkflow_failed"
    sub = N("s", "subworkflow", 0, 0, "s", workflow_id=bad)
    fb = N("fb", "set_variable", 0, 0, "fb", name="mode", value="degraded")
    wid = mk(api, [START, sub, fb, N("end", "end", 0, 0, "e", output="{{ vars.mode }}")], [E("start", "s"), E("s", "end"), E("s", "fb", "error"), E("fb", "end")])
    _, r = run(api, wid)
    assert r["status"] == "succeeded" and r["output"]["result"] == "degraded"
    # map: tolerate item failures when asked
    p = parent_wf(api, {"workflow_id": bad, "mode": "foreach", "items": "input.ns", "on_item_error": "continue", "input": {}})
    _, r = run(api, p, {"ns": [1, 2]})
    assert r["status"] == "succeeded" and r["output"]["result"]["failed"] == 2


def test_cycles_depth_and_limits_are_rejected(api):
    self_id = mk(api, [START, END], [E("start", "end")], name="selfcall")
    d = api.get(f"/api/workflows/{self_id}").json()["draft"]
    d["nodes"].insert(1, N("s", "subworkflow", 0, 0, "s", workflow_id=self_id))
    d["edges"] = [E("start", "s"), E("s", "end")]
    api.put(f"/api/workflows/{self_id}", json={"definition": d})
    _, r = run(api, self_id)
    assert r["status"] == "failed" and r["error"]["code"] == "subworkflow_cycle"
    c = child_wf(api)
    _, r = run(api, parent_wf(api, {"workflow_id": c, "mode": "foreach", "items": "input.ns", "max_items": 2, "input": {"n": "{{ item }}"}}), {"ns": [1, 2, 3]})
    assert r["error"]["code"] == "map_limit_exceeded"
    assert any("max_items" in i["message"] for i in api.get(f"/api/workflows/{mk(api, [START, N('s','subworkflow',0,0,'s',workflow_id=c,mode='foreach',items='input.x',max_items=99), END], [E('start','s'), E('s','end')])}").json()["issues"])


def test_cancelling_parent_cancels_children(api):
    slow = mk(api, [START, N("d", "delay", 0, 0, "d", seconds=60), END], [E("start", "d"), E("d", "end")], name="slow60")
    p = parent_wf(api, {"workflow_id": slow})
    rid = api.post(f"/api/workflows/{p}/run", json={}).json()["run_id"]
    wait_for_run(rid, 10, until={"waiting_for_child"})
    kid = api.get(f"/api/runs/{rid}").json()["children"][0]["id"]
    assert api.post(f"/api/runs/{rid}/cancel").status_code == 200
    assert wait_for_run(rid, 10) == "cancelled" and wait_for_run(kid, 10) == "cancelled"


def test_wake_is_exactly_once_and_replay_uses_fixtures(api):
    from backend.execution import store
    c = child_wf(api)
    rid, r = run(api, parent_wf(api, {"workflow_id": c, "input": {"n": 1}}))
    store.wake_parent(r["children"][0]["id"])                      # late/duplicate wake on a finished parent is a no-op
    assert api.get(f"/api/runs/{rid}").json()["status"] == "succeeded"
    case = api.post("/api/lab/cases", json={"run_id": rid}).json()["id"]
    res = api.post(f"/api/lab/cases/{case}/replay", json={}).json()
    assert res["status"] == "passed" and res["metrics"]["output"]["result"]["result"] == "child:1"
    rr = api.get(f"/api/runs/{res['run_id']}").json()
    assert rr["children"] == []                                    # replay never starts real child runs
