"""Run state machine. Terminal states are final: nothing may leave them."""
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
ACTIVE = {"queued", "running", "retrying", "waiting_for_approval", "waiting_for_child"}

TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    # running -> queued is only used by crash recovery / suspension release (see worker.recover_interrupted)
    "running": {"waiting_for_approval", "waiting_for_child", "retrying", "succeeded", "failed", "cancelled", "timed_out", "queued"},
    "retrying": {"running", "failed", "cancelled", "timed_out", "queued"},
    "waiting_for_approval": {"queued", "cancelled", "failed"},
    "waiting_for_child": {"queued", "cancelled", "failed"},
    "succeeded": set(), "failed": set(), "cancelled": set(), "timed_out": set(),
}


class IllegalTransition(Exception):
    def __init__(self, frm: str, to: str):
        super().__init__(f"illegal run transition {frm} -> {to}")
        self.frm, self.to = frm, to


def check(frm: str, to: str) -> None:
    if to not in TRANSITIONS.get(frm, set()):
        raise IllegalTransition(frm, to)
