"""Durable job runner: polls the database for queued runs. In-memory state is never the source of truth."""
import logging
import threading
import time
import uuid
from datetime import timedelta

from sqlalchemy import update

import os
import socket

from backend.db.models import Run, WorkerRecord
from backend.db.session import now, session
from backend.execution import store
from backend.execution.engine import execute_run
from backend.execution.state import TERMINAL

log = logging.getLogger("flowpilot.worker")


def recover_interrupted(force: bool = False) -> int:
    """Re-queue runs that lost their worker. Completed steps restore from checkpoints, and calls left in flight are treated as
    unknown-outcome (never blindly repeated). `force=True` re-queues EVERY running run: only safe when this is the sole process
    (single-process startup). Otherwise use recover_orphans, which respects other live workers."""
    with session() as s:
        ids = [r.id for r in s.query(Run).filter(Run.status.in_(["running", "retrying"])).all()]
    n = 0
    for rid in ids:
        try:
            store.transition(rid, "queued", event_data={"reason": "recovered after restart"}, lease_owner=None)
            n += 1
        except Exception:  # noqa: BLE001 — finished concurrently
            pass
    return n


def recover_orphans(grace_seconds: float = 20.0) -> int:
    """Re-queue running/retrying runs whose owning worker has stopped heartbeating (or never registered)."""
    cutoff = now() - timedelta(seconds=grace_seconds)
    with session() as s:
        live = {w.id for w in s.query(WorkerRecord).filter(WorkerRecord.last_seen >= cutoff).all()}
        cands = [(r.id, r.lease_owner) for r in s.query(Run).filter(Run.status.in_(["running", "retrying"])).all()]
    n = 0
    for rid, owner in cands:
        if owner in live:
            continue
        try:
            store.transition(rid, "queued", event_data={"reason": "owner worker is gone", "worker": owner}, lease_owner=None)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


class Worker:
    def __init__(self, concurrency: int = 4, poll: float = 0.25):
        self.id = uuid.uuid4().hex[:8]
        self.concurrency, self.poll = concurrency, poll
        self.reap_grace = float(os.environ.get("FLOWPILOT_REAP_GRACE", "20"))
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def _register(self):
        with session() as s:
            s.merge(WorkerRecord(id=self.id, host=socket.gethostname(), pid=os.getpid(), started_at=now(), last_seen=now()))

    def _heartbeat(self):
        while not self.stop.wait(5):
            try:
                with session() as s:
                    s.merge(WorkerRecord(id=self.id, host=socket.gethostname(), pid=os.getpid(), started_at=now(), last_seen=now()))
                recover_orphans(self.reap_grace)
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")

    def start(self):
        self._register()
        threading.Thread(target=self._heartbeat, name="fp-heartbeat", daemon=True).start()
        threading.Thread(target=__import__('backend.execution.scheduler', fromlist=['loop']).loop, args=(self.stop,), name='fp-scheduler', daemon=True).start()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"fp-worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)

    def shutdown(self, timeout: float = 5):
        self.stop.set()
        for t in self.threads:
            t.join(timeout)

    def claim(self) -> str | None:
        with session() as s:
            row = s.query(Run).filter(Run.status == "queued").order_by(Run.created_at).first()
            if not row:
                return None
            res = s.execute(update(Run).where(Run.id == row.id, Run.status == "queued").values(status="running", lease_owner=self.id,
                                                                                               lease_expires=now() + timedelta(seconds=60)))
            if res.rowcount != 1:
                return None
            rid = row.id
        store.emit(rid, "run.claimed", {"worker": self.id})
        return rid

    def _loop(self):
        while not self.stop.is_set():
            try:
                rid = self.claim()
            except Exception:  # noqa: BLE001
                log.exception("claim failed")
                rid = None
            if not rid:
                time.sleep(self.poll)
                continue
            try:
                execute_run(rid)
            except Exception:  # noqa: BLE001
                log.exception("run crashed: %s", rid)
                with session() as s:
                    st = s.get(Run, rid).status
                if st not in TERMINAL:
                    try:
                        store.transition(rid, "failed", error={"code": "worker_crash", "message": "worker crashed"})
                    except Exception:  # noqa: BLE001
                        pass


def wait_for_run(run_id: str, timeout: float = 30, until=TERMINAL | {"waiting_for_approval"}) -> str:
    end = time.time() + timeout
    while time.time() < end:
        with session() as s:
            st = s.get(Run, run_id).status
        if st in until:
            return st
        time.sleep(0.05)
    raise TimeoutError(run_id)
