import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from backend.db.models import Run
from backend.db.session import session
from backend.execution import store


class Suspend(Exception):
    """Run must pause (approval / reconciliation pending). Not an error."""

    def __init__(self, approval_id: str):
        self.approval_id = approval_id


class SuspendForChildren(Exception):
    """Parent run pauses until its child runs finish (releases the worker: no nested blocking, no deadlock)."""


class NodeFailed(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def to_dict(self):
        return {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}


class RunCancelled(Exception):
    pass


class RunTimedOut(Exception):
    pass


class FixtureMiss(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode()).hexdigest()[:32]


@dataclass
class Step:
    """State of the node execution currently in flight."""
    node: dict
    visit: int
    seq: int = 0
    attempts: list = field(default_factory=list)
    input: Any = None
    usage_llm: int = 0

    @property
    def node_id(self) -> str:
        return self.node["id"]

    def next_seq(self) -> int:
        s = self.seq
        self.seq += 1
        return s


class Exec:
    """Everything one worker needs while driving a run."""

    def __init__(self, run: Run, defn: dict, settings: dict):
        self.run_id = run.id
        self.workflow_id = run.workflow_id
        self.mode = run.mode
        self.options = run.options or {}
        self.defn = defn
        self.nodes = {n["id"]: n for n in defn["nodes"]}
        self.out: dict[tuple[str, str], str] = {}
        self.in_edges: dict[str, list[str]] = {}
        for e in defn["edges"]:
            self.out[(e["source"], e.get("sourceHandle") or "out")] = e["target"]
            self.in_edges.setdefault(e["target"], []).append(e["source"])
        pol = defn.get("policy") or {}
        self.settings = settings
        self.max_steps = min(int(pol.get("max_steps", settings["max_steps"])), settings["max_steps"])
        self.max_seconds = min(int(pol.get("max_run_seconds", settings["max_run_seconds"])), settings["max_run_seconds"])
        self.require_gate = bool(settings["require_approval_for_side_effects"])
        self.active_before = float((run.usage or {}).get("active_seconds", 0))
        self.t0 = time.monotonic()
        self.ctx: dict = {"input": run.input or {}, "vars": {}, "nodes": {}, "loop": {}, "run": {"id": run.id}}
        self.fixtures: list = self.options.get("fixtures", [])
        self.faults: list = self.options.get("faults", [])
        self.step: Step | None = None
        self.sig_counts: dict[str, int] = {}
        self.stats = {"llm_calls": 0, "tool_calls": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0, "tokens_reported": False}

    # --- budgets ---------------------------------------------------------
    def elapsed(self) -> float:
        return self.active_before + (time.monotonic() - self.t0)

    def check(self):
        with session() as s:
            c = s.query(Run.cancel_requested).filter_by(id=self.run_id).scalar()
        if c:
            raise RunCancelled()
        if self.elapsed() > self.max_seconds:
            raise RunTimedOut()

    def sleep(self, seconds: float):
        """Cancel-aware sleep in 200ms ticks. Replays never actually wait."""
        if self.mode == "fixture_replay":
            return
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))

    def emit(self, type_: str, data: dict | None = None, node_id: str | None = None):
        return store.emit(self.run_id, type_, data, node_id or (self.step.node_id if self.step else None))

    def env(self) -> dict:
        return {"input": self.ctx["input"], "vars": self.ctx["vars"], "nodes": self.ctx["nodes"], "loop": self.ctx["loop"], "run": self.ctx["run"]}
