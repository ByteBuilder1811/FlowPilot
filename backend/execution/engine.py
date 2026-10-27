"""Run driver. Deterministic re-walk of the graph: completed steps are restored from their persisted records
(never re-executed), so a run can resume after approval, crash or restart without repeating work."""
import traceback
from datetime import timedelta

from backend.db.models import Run, RunStep, WorkflowVersion
from backend.db.session import new_id, now, session
from backend.execution import store
from backend.execution.context import Exec, FixtureMiss, NodeFailed, RunCancelled, RunTimedOut, Step, Suspend, SuspendForChildren, sha
from backend.execution.nodes import HANDLERS
from backend.execution.settings_store import get_settings
from backend.security.redaction import redact


def _apply_patch(ex: Exec, patch: dict | None):
    for k, v in (patch or {}).items():
        if k == "vars":
            ex.ctx["vars"].update(v)
        elif k == "loop":
            for nid, st in v.items():
                if st is None:
                    ex.ctx["loop"].pop(nid, None)
                else:
                    ex.ctx["loop"][nid] = st


def _step_row(run_id, node_id, visit):
    with session() as s:
        return s.query(RunStep).filter_by(run_id=run_id, node_id=node_id, visit=visit).first()


def _save_usage(ex: Exec):
    with session() as s:
        r = s.get(Run, ex.run_id)
        u = dict(r.usage or {})
        u.update({k: ex.stats[k] for k in ("llm_calls", "tool_calls", "retries")})
        u["tokens_reported"] = ex.stats["tokens_reported"]
        u["input_tokens"] = ex.stats["input_tokens"] if ex.stats["tokens_reported"] else None
        u["output_tokens"] = ex.stats["output_tokens"] if ex.stats["tokens_reported"] else None
        u["cost_usd"] = None  # no reliable pricing source: never fabricate
        u["active_seconds"] = round(ex.elapsed(), 3)
        r.usage = u
        r.step_count = ex.ctx.get("_steps", 0)


def _finish(ex: Exec, status: str, **fields):
    _save_usage(ex)
    store.transition(ex.run_id, status, **fields)


def execute_run(run_id: str) -> None:
    with session() as s:
        run = s.get(Run, run_id)
        ver = s.get(WorkflowVersion, run.version_id)
        defn = ver.definition
        s.expunge(run)
    ex = Exec(run, defn, get_settings())
    ex.ctx["_steps"] = 0
    with session() as s:
        r = s.get(Run, run_id)
        first = r.started_at is None
        if first:
            r.started_at = now()
    ex.emit("run.started" if first else "run.resumed", {"mode": run.mode, "workflow_version": ver.version_no})
    start = next(n for n in defn["nodes"] if n["type"] == "start")
    cursor: str | None = start["id"]
    visits: dict[str, int] = {}
    try:
        while cursor:
            ex.check()
            node = ex.nodes[cursor]
            visit = visits.get(cursor, 0)
            visits[cursor] = visit + 1
            ex.ctx["_steps"] += 1
            if ex.ctx["_steps"] > ex.max_steps:
                raise NodeFailed("max_steps_exceeded", f"Run exceeded the step budget of {ex.max_steps}", {"max_steps": ex.max_steps})
            with session() as s:
                s.query(Run).filter_by(id=run_id).update({"current_node": cursor, "lease_expires": now() + timedelta(seconds=60)})
            row = _step_row(run_id, cursor, visit)
            if row and row.status == "succeeded":  # restored from checkpoint
                ex.ctx["nodes"][cursor] = row.output
                _apply_patch(ex, row.ctx_patch)
                if node["type"] == "end":
                    _finish(ex, "succeeded", output=row.output)
                    return
                cursor = ex.out.get((cursor, row.handle or "out"))
                continue
            if row and row.status == "failed" and row.handle == "error":
                ex.ctx["nodes"][cursor] = row.output
                cursor = ex.out.get((cursor, "error"))
                continue
            step = Step(node=node, visit=visit)
            ex.step = step
            if row is None:
                with session() as s:
                    s.add(RunStep(id=new_id("stp"), run_id=run_id, node_id=cursor, node_type=node["type"], visit=visit, status="running", attempts=[], started_at=now()))
                ex.emit("step.started", {"node_type": node["type"], "label": node["data"].get("label"), "visit": visit})
            else:
                with session() as s:
                    s.query(RunStep).filter_by(run_id=run_id, node_id=cursor, visit=visit).update({"status": "running"})
                ex.emit("step.resumed", {"node_type": node["type"], "visit": visit})
            try:
                output, handle, patch = HANDLERS[node["type"]](ex, step)
            except Suspend as sp:
                _persist(run_id, cursor, visit, step, status="waiting")
                _save_usage(ex)
                store.transition(run_id, "waiting_for_approval", event_data={"approval_id": sp.approval_id, "node_id": cursor})
                return
            except SuspendForChildren:
                _persist(run_id, cursor, visit, step, status="waiting")
                _save_usage(ex)
                store.transition(run_id, "waiting_for_child", event_data={"node_id": cursor})
                store.wake_parent_check(run_id)
                return
            except NodeFailed as nf:
                _persist(run_id, cursor, visit, step, status="failed", error=nf.to_dict())
                if (cursor, "error") in ex.out and node["type"] in ("llm", "tool", "http", "transform", "subworkflow") and nf.code not in ("approval_rejected", "loop_detected"):
                    out = {"error": nf.to_dict()}
                    _persist(run_id, cursor, visit, step, status="failed", error=nf.to_dict(), output=out, handle="error")
                    ex.ctx["nodes"][cursor] = redact(out)
                    ex.emit("step.recovered", {"via": "error_path", "error": nf.to_dict()})
                    cursor = ex.out[(cursor, "error")]
                    continue
                ex.emit("step.failed", {"error": nf.to_dict()})
                raise
            output = redact(output)
            _persist(run_id, cursor, visit, step, status="succeeded", output=output, handle=handle, ctx_patch=patch)
            ex.ctx["nodes"][cursor] = output
            _apply_patch(ex, patch)
            ex.emit("step.succeeded", {"output": output, "handle": handle, "attempts": len(step.attempts)})
            _save_usage(ex)
            if node["type"] == "end":
                _finish(ex, "succeeded", output=output)
                ex.emit("run.output", {"output": output})
                return
            cursor = ex.out.get((cursor, handle or "out"))
        _finish(ex, "succeeded", output={"result": None, "note": "workflow ended without reaching an End node"})
    except RunCancelled:
        ex.emit("run.cancel_acknowledged", {"at_node": ex.step.node_id if ex.step else None})
        _finish(ex, "cancelled", error={"code": "cancelled", "message": "Cancelled on request"})
    except RunTimedOut:
        _finish(ex, "timed_out", error={"code": "run_timeout", "message": f"Run exceeded {ex.max_seconds}s of active time"})
    except FixtureMiss as fm:
        _finish(ex, "failed", error={"code": "fixture_miss", "message": f"No recorded fixture for {fm.what}. The replay is inconclusive, not a pass or fail of the workflow logic."})
    except NodeFailed as nf:
        _finish(ex, "failed", error={**nf.to_dict(), "node_id": ex.step.node_id if ex.step else None})
    except Exception as e:  # noqa: BLE001 — never leave a run stuck in `running`
        tb = traceback.format_exc(limit=6)
        ex.emit("run.internal_error", {"error": type(e).__name__, "trace": tb[-1500:]})
        _finish(ex, "failed", error={"code": "internal_error", "message": f"{type(e).__name__}: {e}"[:300]})


def _persist(run_id, node_id, visit, step: Step, *, status, output=None, error=None, handle=None, ctx_patch=None):
    with session() as s:
        r = s.query(RunStep).filter_by(run_id=run_id, node_id=node_id, visit=visit).one()
        r.status, r.attempts = status, redact(step.attempts)
        r.input = redact(step.input)
        if output is not None:
            r.output = output
        if error is not None:
            r.error = redact(error)
        r.handle, r.ctx_patch = handle, ctx_patch
        if status in ("succeeded", "failed"):
            r.ended_at = now()
