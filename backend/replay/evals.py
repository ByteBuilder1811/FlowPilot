"""Scored evals: run a dataset through a workflow version (LIVE runs) and score each output.
Scorers: exact, contains, regex, json_subset, judge (LLM-as-judge; the mock judge is a labelled keyword heuristic)."""
import json
import re
import time

from backend.db.models import EvalDataset, EvalResult, EvalRow, EvalRun, Run, WorkflowVersion
from backend.db.session import new_id, now, session
from backend.execution import store
from backend.execution.state import TERMINAL
from backend.execution.worker import wait_for_run
from backend.providers.base import ProviderError
from backend.providers.registry import get_provider
from backend.security.redaction import redact
from backend.workflows.service import WorkflowError

MAX_ROWS = 25
SCORERS = ["exact", "contains", "regex", "json_subset", "judge"]


def _result_value(output: dict | None):
    return (output or {}).get("result")


def _subset(exp, got) -> bool:
    if isinstance(exp, dict):
        return isinstance(got, dict) and all(k in got and _subset(v, got[k]) for k, v in exp.items())
    return exp == got


def judge_score(judge: dict, rubric: str, output, expected) -> dict:
    provider = (judge or {}).get("provider", "mock")
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    if provider == "mock":
        words = {w for w in re.findall(r"[a-z0-9]{4,}", rubric.lower())}
        hit = sum(1 for w in words if w in text.lower())
        score = round(hit / len(words), 2) if words else 0.0
        return {"scorer": "judge", "score": score, "passed": score >= float((judge or {}).get("threshold", 0.6)), "judge": "mock-heuristic",
                "detail": f"MOCK judge: {hit}/{len(words)} rubric keywords present (not a real model judgment)"}
    try:
        prompt = (f"You are a strict evaluator. Rubric:\n{rubric}\n\nExpected (may be empty): {json.dumps(expected)}\n\nOutput to grade:\n{text[:6000]}\n\n"
                  'Reply with ONLY JSON: {"score": <0..1>, "reason": "<one sentence>"}')
        res = get_provider(provider).complete(model=judge.get("model") or "", system="", messages=[{"role": "user", "content": prompt}], tools=[], timeout=60)
        m = re.search(r"\{.*\}", res.text, re.S)
        data = json.loads(m.group(0)) if m else {}
        score = max(0.0, min(1.0, float(data["score"])))
        return {"scorer": "judge", "score": score, "passed": score >= float(judge.get("threshold", 0.6)), "judge": f"{provider}/{judge.get('model')}", "detail": str(data.get("reason", ""))[:300]}
    except (ProviderError, ValueError, KeyError, TypeError) as e:
        return {"scorer": "judge", "score": None, "passed": False, "judge": provider, "detail": f"judge unavailable: {getattr(e, 'message', e)}"}


def score_row(scorers: list[str], judge: dict | None, expected: dict, output: dict | None) -> list[dict]:
    val = _result_value(output)
    blob = val if isinstance(val, str) else json.dumps(val, default=str)
    out = []
    for sc in scorers:
        if sc == "exact":
            ok = "output" in expected and val == expected["output"]
            out.append({"scorer": sc, "passed": ok, "score": 1.0 if ok else 0.0, "detail": "matches expected.output" if ok else f"expected {json.dumps(expected.get('output'))[:120]}"})
        elif sc == "contains":
            needle = expected.get("contains")
            ok = needle is not None and str(needle).lower() in blob.lower()
            out.append({"scorer": sc, "passed": ok, "score": 1.0 if ok else 0.0, "detail": f"contains '{needle}'" if ok else f"missing '{needle}'"})
        elif sc == "regex":
            try:
                ok = bool(expected.get("regex")) and re.search(str(expected["regex"])[:200], blob[:20000]) is not None
            except re.error:
                ok = False
            out.append({"scorer": sc, "passed": ok, "score": 1.0 if ok else 0.0, "detail": f"regex {expected.get('regex')}"})
        elif sc == "json_subset":
            ok = "json" in expected and _subset(expected["json"], val)
            out.append({"scorer": sc, "passed": ok, "score": 1.0 if ok else 0.0, "detail": "expected.json is a subset of the output" if ok else "subset mismatch"})
        elif sc == "judge":
            out.append(judge_score(judge or {}, str(expected.get("rubric") or (judge or {}).get("rubric") or ""), val, expected))
    return out


def run_eval(dataset_id: str, version_id: str, scorers: list[str], judge: dict | None, per_row_timeout: float = 30) -> dict:
    bad = [s for s in scorers if s not in SCORERS]
    if bad or not scorers:
        raise WorkflowError("bad_scorers", f"Choose scorers from {', '.join(SCORERS)}")
    with session() as s:
        ds = s.get(EvalDataset, dataset_id)
        if not ds:
            raise WorkflowError("not_found", "Dataset not found", status=404)
        v = s.get(WorkflowVersion, version_id)
        if not v or v.workflow_id != ds.workflow_id:
            raise WorkflowError("bad_version", "Version does not belong to the dataset's workflow")
        rows = s.query(EvalRow).filter_by(dataset_id=dataset_id).all()
        rows = [(r.id, r.input, r.expected) for r in rows]
        wid = ds.workflow_id
    if not rows:
        raise WorkflowError("empty_dataset", "Add rows first")
    if len(rows) > MAX_ROWS:
        raise WorkflowError("too_large", f"At most {MAX_ROWS} rows per eval run")
    eid = new_id("eval")
    with session() as s:
        s.add(EvalRun(id=eid, dataset_id=dataset_id, version_id=version_id, scorers=scorers, judge=judge, created_at=now()))
    results = []
    for rid_, inp, exp in rows:
        run_id = store.create_run(wid, version_id, inp, trigger="eval")
        status = wait_for_run(run_id, per_row_timeout, until=TERMINAL | {"waiting_for_approval"}) if True else None
        with session() as s:
            r = s.get(Run, run_id)
            out, st, dur = r.output, r.status, ((r.completed_at - r.started_at).total_seconds() * 1000 if r.completed_at and r.started_at else None)
        if st == "waiting_for_approval":  # evals never bypass approvals; the row is reported as blocked and the run is cancelled
            try:
                store.transition(run_id, "cancelled", error={"code": "eval_blocked", "message": "Eval run reached an approval gate"}, quiet=False)
            except Exception:  # noqa: BLE001
                pass
            status_, scores = "blocked", []
        elif st != "succeeded":
            status_, scores = "errored", []
        else:
            scores = score_row(scorers, judge, exp, out)
            status_ = "passed" if all(x["passed"] for x in scores) else "failed"
        results.append({"row_id": rid_, "run_id": run_id, "status": status_, "scores": scores, "output": redact(out), "duration_ms": round(dur) if dur else None})
    with session() as s:
        for r in results:
            s.add(EvalResult(id=new_id("evr"), eval_run_id=eid, **r))
        summary = {k: sum(1 for r in results if r["status"] == k) for k in ("passed", "failed", "errored", "blocked")}
        summary["rows"] = len(results)
        s.query(EvalRun).filter_by(id=eid).update({"summary": summary})
    return {"id": eid, "summary": summary}
