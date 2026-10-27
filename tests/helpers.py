from backend.execution.worker import wait_for_run
from backend.execution.state import TERMINAL
from backend.workflows.examples import E, N


def mk(api, nodes, edges, name="t", policy=None):
    d = {"format": "flowpilot.workflow/v1", "nodes": nodes, "edges": edges, "policy": policy or {}}
    r = api.post("/api/workflows", json={"name": name, "definition": d})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def run(api, wid, inp=None, until=TERMINAL, timeout=20):
    r = api.post(f"/api/workflows/{wid}/run", json={"input": inp or {}})
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    wait_for_run(rid, timeout, until=until)
    return rid, api.get(f"/api/runs/{rid}").json()


START = N("start", "start", 0, 0, "Start")
END = N("end", "end", 0, 0, "End", output="{{ nodes }}")
