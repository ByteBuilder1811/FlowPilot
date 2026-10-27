"""Persistence helpers for runs, events and state transitions (all writes go through here)."""
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from backend.db.models import ExecutionEvent, Run
from backend.db.session import new_id, now, session
from backend.execution import state
from backend.security.redaction import redact


def emit(run_id: str, type_: str, data: dict | None = None, node_id: str | None = None) -> int:
    """Append an event with a per-run monotonically increasing seq. Data is redacted before it is stored."""
    payload = redact(data or {})
    for _ in range(8):
        try:
            with session() as s:
                seq = (s.query(func.max(ExecutionEvent.seq)).filter_by(run_id=run_id).scalar() or 0) + 1
                s.add(ExecutionEvent(run_id=run_id, seq=seq, type=type_, node_id=node_id, data=payload, ts=now()))
            return seq
        except IntegrityError:
            continue
    raise RuntimeError("could not allocate event sequence")


def events_after(run_id: str, after: int = 0, limit: int = 500) -> list[dict]:
    with session() as s:
        rows = s.query(ExecutionEvent).filter(ExecutionEvent.run_id == run_id, ExecutionEvent.seq > after).order_by(ExecutionEvent.seq).limit(limit).all()
        return [{"seq": r.seq, "type": r.type, "node_id": r.node_id, "data": r.data, "ts": r.ts.isoformat()} for r in rows]


def transition(run_id: str, to: str, *, event_data: dict | None = None, quiet: bool = False, **fields) -> str:
    """Atomically move a run to `to` after validating legality. Returns the previous status."""
    with session() as s:
        run = s.get(Run, run_id, with_for_update=False)
        if run is None:
            raise KeyError(run_id)
        frm = run.status
        state.check(frm, to)
        run.status = to
        for k, v in fields.items():
            setattr(run, k, v)
        if to in state.TERMINAL and run.completed_at is None:
            run.completed_at = now()
    if not quiet:
        emit(run_id, f"run.{to}", {"from": frm, **(event_data or {})})
    if to in state.TERMINAL:
        wake_parent(run_id)
    return frm


def create_run(workflow_id: str, version_id: str, input_: dict, *, mode: str = "live", trigger: str = "manual", options: dict | None = None,
               source_run_id: str | None = None, case_id: str | None = None, parent_run_id: str | None = None, parent_key: str | None = None, depth: int = 0) -> str:
    rid = new_id("run")
    with session() as s:
        s.add(Run(id=rid, workflow_id=workflow_id, version_id=version_id, status="queued", mode=mode, trigger=trigger,
                  input=redact(input_), options=options or {}, source_run_id=source_run_id, case_id=case_id, created_at=now(), parent_run_id=parent_run_id, parent_key=parent_key, depth=depth,
                  usage={"llm_calls": 0, "tool_calls": 0, "retries": 0, "input_tokens": None, "output_tokens": None, "cost_usd": None}))
    emit(rid, "run.queued", {"mode": mode, "trigger": trigger, "workflow_id": workflow_id, "version_id": version_id})
    return rid


def wake_parent(child_id: str) -> None:
    """When a child finishes, resume its parent iff no sibling is still active. Compare-and-set: resumes at most once."""
    from sqlalchemy import update
    with session() as s:
        child = s.get(Run, child_id)
        if not child or not child.parent_run_id:
            return
        pid = child.parent_run_id
        active = s.query(Run).filter(Run.parent_run_id == pid, Run.status.in_(list(state.ACTIVE))).count()
        if active:
            return
        res = s.execute(update(Run).where(Run.id == pid, Run.status == "waiting_for_child").values(status="queued"))
        woke = res.rowcount == 1
    if woke:
        emit(pid, "run.queued", {"from": "waiting_for_child", "reason": "child runs finished"})


def wake_parent_check(parent_id: str) -> None:
    """Lost-wakeup guard: if every child already finished while the parent was still going to sleep, wake it now."""
    from sqlalchemy import update
    with session() as s:
        active = s.query(Run).filter(Run.parent_run_id == parent_id, Run.status.in_(list(state.ACTIVE))).count()
        if active:
            return
        res = s.execute(update(Run).where(Run.id == parent_id, Run.status == "waiting_for_child").values(status="queued"))
        woke = res.rowcount == 1
    if woke:
        emit(parent_id, "run.queued", {"from": "waiting_for_child", "reason": "child runs finished"})
