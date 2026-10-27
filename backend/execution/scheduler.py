"""Cron scheduler. Fires at most once per workflow per UTC minute (DB compare-and-set), never backfills missed minutes."""
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import or_, update

from backend.db.models import Workflow
from backend.db.session import session
from backend.execution import store
from backend.workflows import cron, service

log = logging.getLogger("flowpilot.scheduler")


def start_cfg(draft: dict) -> dict | None:
    n = next((n for n in draft.get("nodes", []) if n.get("type") == "start"), None)
    return n["data"].get("config", {}) if n else None


def tick(now_dt: datetime | None = None) -> list[str]:
    now_dt = (now_dt or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    key = now_dt.strftime("%Y%m%d%H%M")
    fired = []
    with session() as s:
        cands = [(w.id, w.draft) for w in s.query(Workflow).filter_by(schedule_enabled=True, deleted=False).all()]
    for wid, draft in cands:
        cfg = start_cfg(draft) or {}
        if cfg.get("trigger") != "schedule":
            continue
        try:
            if not cron.matches(cfg.get("cron", ""), now_dt):
                continue
        except cron.CronError:
            continue
        with session() as s:
            res = s.execute(update(Workflow).where(Workflow.id == wid, or_(Workflow.last_fired_minute.is_(None), Workflow.last_fired_minute != key)).values(last_fired_minute=key))
            if res.rowcount != 1:
                continue
        try:
            v = service.snapshot_version(wid, "Snapshot for scheduled run")
            fired.append(store.create_run(wid, v["id"], dict(cfg.get("schedule_input") or {}), trigger="schedule"))
        except service.WorkflowError as e:
            log.warning("schedule for %s not fired: %s", wid, e.message)
    return fired


def loop(stop: threading.Event, every: float = 15.0):
    while not stop.wait(every):
        try:
            tick()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
