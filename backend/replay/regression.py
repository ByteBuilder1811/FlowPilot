"""Failure Lab: fixtures, regression cases, deterministic replay, fault injection and version comparison."""
import hashlib
import json

from backend.db.models import ApprovalRequest, RegressionCase, RegressionResult, Run, RunStep, ToolCall, WorkflowVersion
from backend.db.session import new_id, now, session
from backend.execution import store
from backend.execution.state import TERMINAL
from backend.execution.worker import wait_for_run
from backend.security.redaction import redact
from backend.workflows.service import WorkflowError, diff_definitions

FAULT_KINDS = ["timeout", "rate_limit", "transient_error", "malformed_response"]


def build_fixtures(run_id: str) -> tuple[list, list]:
    """Sanitized fixtures from a recorded run: every tool/LLM outcome, plus the human decisions."""
    with session() as s:
        calls = s.query(ToolCall).filter_by(run_id=run_id).order_by(ToolCall.started_at, ToolCall.seq).all()
        fixtures = []
        for c in calls:
            if c.outcome == "started":
                continue
            fixtures.append(redact({"node_id": c.node_id, "kind": c.kind, "tool": c.tool, "ordinal": c.ordinal, "args_hash": c.args_hash,
                                    "outcome": "ok" if c.outcome == "ok" else "error", "result": c.result, "error": c.error}))
        aps = s.query(ApprovalRequest).filter_by(run_id=run_id).filter(ApprovalRequest.status != "pending").all()
        approvals = [{"node_id": a.node_id, "kind": a.kind, "status": a.status, **({"action": a.decision.get("action"), "output": a.decision.get("output")} if a.decision else {})} for a in aps]
    return fixtures, approvals


def default_expect(run: Run) -> dict:
    # A failed/cancelled source run is a bug report: the expectation is the outcome we WANT (success).
    return {"status": "succeeded", "no_unapproved_side_effects": True, "output_contains": None, "max_retries": None, "max_tool_calls": None, "approval_default": "approve"}


def create_case(run_id: str, name: str | None = None, expect: dict | None = None, notes: str = "", faults: list | None = None) -> dict:
    with session() as s:
        run = s.get(Run, run_id)
        if not run:
            raise WorkflowError("not_found", "Run not found", status=404)
        if run.status not in TERMINAL:
            raise WorkflowError("run_not_finished", "Only finished runs can become regression cases", status=409)
        wid, vid, inp, status = run.workflow_id, run.version_id, run.input, run.status
        s.expunge(run)
    fixtures, approvals = build_fixtures(run_id)
    cid = new_id("case")
    ex = {**default_expect(run), **(expect or {})}
    with session() as s:
        s.add(RegressionCase(id=cid, name=(name or f"{status} run {run_id[-6:]}")[:200], workflow_id=wid, version_id=vid, source_run_id=run_id, input=inp,
                             fixtures=fixtures, approvals=approvals, faults=faults or [], expect=ex, notes=notes, created_at=now()))
    return {"id": cid, "fixtures": len(fixtures)}


def case_dict(c: RegressionCase) -> dict:
    return {"id": c.id, "name": c.name, "workflow_id": c.workflow_id, "version_id": c.version_id, "source_run_id": c.source_run_id, "input": c.input,
            "fixtures": c.fixtures, "approvals": c.approvals, "faults": c.faults, "expect": c.expect, "notes": c.notes, "created_at": c.created_at.isoformat()}


def _metrics(run_id: str) -> dict:
    with session() as s:
        run = s.get(Run, run_id)
        calls = s.query(ToolCall).filter_by(run_id=run_id).all()
        steps = s.query(RunStep).filter_by(run_id=run_id).count()
        dur = ((run.completed_at - run.started_at).total_seconds() * 1000) if run.completed_at and run.started_at else None
        u = run.usage or {}
        return {"status": run.status, "duration_ms": round(dur) if dur is not None else None, "steps": steps,
                "tool_calls": sum(1 for c in calls if c.kind == "tool"), "llm_calls": sum(1 for c in calls if c.kind == "llm"),
                "retries": u.get("retries", 0), "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"), "cost_usd": u.get("cost_usd"),
                "unapproved_side_effects": sum(1 for c in calls if c.side_effect and c.outcome == "ok" and not c.approval_id),
                "error": run.error, "output": run.output}


def evaluate(expect: dict, m: dict) -> tuple[str, list]:
    if m["error"] and m["error"].get("code") == "fixture_miss":
        return "inconclusive", [{"name": "fixtures", "passed": None, "detail": m["error"]["message"]}]
    checks = []
    if expect.get("status"):
        checks.append({"name": "terminal status", "passed": m["status"] == expect["status"], "expected": expect["status"], "actual": m["status"]})
    if expect.get("no_unapproved_side_effects", True):
        checks.append({"name": "no unapproved side effects", "passed": m["unapproved_side_effects"] == 0, "expected": 0, "actual": m["unapproved_side_effects"]})
    if expect.get("output_contains"):
        blob = str(m["output"])
        checks.append({"name": "output contains", "passed": str(expect["output_contains"]) in blob, "expected": expect["output_contains"], "actual": blob[:200]})
    if expect.get("max_retries") is not None:
        checks.append({"name": "retries ≤ limit", "passed": m["retries"] <= expect["max_retries"], "expected": expect["max_retries"], "actual": m["retries"]})
    if expect.get("max_tool_calls") is not None:
        checks.append({"name": "tool calls ≤ limit", "passed": m["tool_calls"] <= expect["max_tool_calls"], "expected": expect["max_tool_calls"], "actual": m["tool_calls"]})
    return ("passed" if all(c["passed"] for c in checks) else "failed"), checks


def run_case(case_id: str, version_id: str | None = None, extra_faults: list | None = None, batch_id: str | None = None, timeout: float = 60) -> dict:
    with session() as s:
        c = s.get(RegressionCase, case_id)
        if not c:
            raise WorkflowError("not_found", "Case not found", status=404)
        vid = version_id or c.version_id
        v = s.get(WorkflowVersion, vid)
        if not v or v.workflow_id != c.workflow_id:
            raise WorkflowError("bad_version", "Version does not belong to the case's workflow")
        opts = {"fixtures": c.fixtures, "approvals": c.approvals, "faults": (c.faults or []) + (extra_faults or []), "replay_of_case": c.id, "approval_default": (c.expect or {}).get("approval_default", "approve")}
        wid, inp, expect = c.workflow_id, c.input, c.expect
    rid = store.create_run(wid, vid, inp, mode="fixture_replay", trigger="regression", options=opts, case_id=case_id)
    wait_for_run(rid, timeout, until=TERMINAL)
    m = _metrics(rid)
    status, checks = evaluate(expect, m)
    res_id = new_id("res")
    with session() as s:
        s.add(RegressionResult(id=res_id, case_id=case_id, version_id=vid, batch_id=batch_id, run_id=rid, status=status, checks=checks, metrics=redact(m), created_at=now()))
    return {"id": res_id, "case_id": case_id, "version_id": vid, "run_id": rid, "status": status, "checks": checks, "metrics": redact(m)}


def classify(a: str, b: str) -> str:
    if a == b:
        return {"passed": "unchanged_pass", "failed": "unchanged_fail"}.get(a, "inconclusive")
    if a == "passed" and b == "failed":
        return "regression"
    if a == "failed" and b == "passed":
        return "improvement"
    return "inconclusive"


def compare_versions(workflow_id: str, va: str, vb: str, case_ids: list[str] | None = None) -> dict:
    with session() as s:
        A, B = s.get(WorkflowVersion, va), s.get(WorkflowVersion, vb)
        if not A or not B or A.workflow_id != workflow_id or B.workflow_id != workflow_id:
            raise WorkflowError("bad_version", "Both versions must belong to the workflow")
        q = s.query(RegressionCase).filter_by(workflow_id=workflow_id)
        cases = [c for c in q.all() if not case_ids or c.id in case_ids]
        structural = diff_definitions(A.definition, B.definition)
        info = {"a": {"id": A.id, "version_no": A.version_no, "note": A.note}, "b": {"id": B.id, "version_no": B.version_no, "note": B.note}}
    batch = new_id("batch")
    rows = []
    for c in cases:
        ra, rb = run_case(c.id, va, batch_id=batch), run_case(c.id, vb, batch_id=batch)
        ma, mb = ra["metrics"], rb["metrics"]
        rows.append({"case_id": c.id, "name": c.name, "a": ra, "b": rb, "change": classify(ra["status"], rb["status"]),
                     "delta": {k: (None if ma.get(k) is None or mb.get(k) is None else mb[k] - ma[k]) for k in ("duration_ms", "steps", "tool_calls", "llm_calls", "retries")},
                     "new_safety_violations": max(0, mb["unapproved_side_effects"] - ma["unapproved_side_effects"])})
    summary = {k: sum(1 for r in rows if r["change"] == k) for k in ("regression", "improvement", "unchanged_pass", "unchanged_fail", "inconclusive")}
    summary["new_safety_violations"] = sum(r["new_safety_violations"] for r in rows)
    summary["sample_size"] = len(rows)
    return {"batch_id": batch, **info, "structural": structural, "cases": rows, "summary": summary,
            "unavailable": ["token usage and cost (mock providers report no reliable token data)"] if all(r["a"]["metrics"]["input_tokens"] is None for r in rows) else []}


def _fingerprint(run_id: str) -> str:
    """Hash of everything that should be identical across deterministic replays (ids/timestamps excluded)."""
    with session() as s:
        run = s.get(Run, run_id)
        steps = s.query(RunStep).filter_by(run_id=run_id).order_by(RunStep.started_at, RunStep.node_id).all()
        calls = s.query(ToolCall).filter_by(run_id=run_id).order_by(ToolCall.started_at, ToolCall.seq).all()
        blob = {"status": run.status, "error": (run.error or {}).get("code"), "output": run.output,
                "steps": [(t.node_id, t.visit, t.status, t.handle, t.output) for t in steps],
                "calls": [(c.node_id, c.tool, c.seq, c.outcome, c.source, c.result, c.error) for c in calls]}
    text = json.dumps(blob, sort_keys=True, default=str).replace(run_id, "<run>")
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def determinism_check(case_id: str, runs: int = 5, version_id: str | None = None) -> dict:
    results = [run_case(case_id, version_id) for _ in range(runs)]
    fps = [_fingerprint(r["run_id"]) for r in results]
    return {"case_id": case_id, "runs": runs, "identical": len(set(fps)) == 1, "distinct_fingerprints": len(set(fps)), "fingerprints": fps,
            "statuses": [r["status"] for r in results], "run_ids": [r["run_id"] for r in results]}


def version_matrix(workflow_id: str, case_ids: list[str] | None = None, max_runs: int = 60) -> dict:
    """Replay every case on every version (oldest→newest) and pinpoint the version where each case first broke."""
    with session() as s:
        vs = s.query(WorkflowVersion).filter_by(workflow_id=workflow_id).order_by(WorkflowVersion.version_no).all()
        defs = {v.id: v.definition for v in vs}
        versions = [{"id": v.id, "version_no": v.version_no, "note": v.note} for v in vs]
        cases = [c for c in s.query(RegressionCase).filter_by(workflow_id=workflow_id).all() if not case_ids or c.id in case_ids]
        cases = [{"id": c.id, "name": c.name} for c in cases]
    if len(versions) * len(cases) > max_runs:
        raise WorkflowError("too_large", f"{len(versions)} versions × {len(cases)} cases exceeds the {max_runs}-replay limit; select fewer cases")
    batch = new_id("batch")
    rows = []
    for c in cases:
        cells, culprit = {}, None
        prev = None
        for i, v in enumerate(versions):
            r = run_case(c["id"], v["id"], batch_id=batch)
            cells[v["id"]] = {"status": r["status"], "run_id": r["run_id"], "retries": r["metrics"]["retries"], "steps": r["metrics"]["steps"]}
            if culprit is None and prev == "passed" and r["status"] == "failed":
                culprit = {"version_id": v["id"], "version_no": v["version_no"], "diff": diff_definitions(defs[versions[i - 1]["id"]], defs[v["id"]])}
            if r["status"] != "inconclusive":
                prev = r["status"]
        rows.append({"case_id": c["id"], "name": c["name"], "cells": cells, "regressed_in": culprit})
    return {"batch_id": batch, "versions": versions, "cases": rows}
