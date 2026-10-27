from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    """kind: timeout|rate_limit|unavailable|bad_request|malformed_response|forbidden|blocked|internal.
    executed: True = side effect definitely happened, False = definitely did not, None = unknown."""

    def __init__(self, kind: str, message: str, transient: bool = False, executed: bool | None = False):
        super().__init__(message)
        self.kind, self.message, self.transient, self.executed = kind, message, transient, executed

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "transient": self.transient, "executed": self.executed}


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[["CallInfo", dict], Any]
    side_effect: bool = False
    supports_idempotency_key: bool = False
    replay_safe: bool = False  # pure & deterministic: may run for real during fixture replay if no fixture was recorded
    mock: bool = True
    credentials: tuple = ()  # env var names the connector needs; values are never stored or returned
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def public(self) -> dict:
        import os
        from backend.security import vault
        configured = (all(vault.configured(c) for c in self.credentials) if self.credentials else None)
        return {"credentials": list(self.credentials), "configured": configured, "name": self.name, "description": self.description, "side_effect": self.side_effect, "mock": self.mock,
                "supports_idempotency_key": self.supports_idempotency_key, "replay_safe": self.replay_safe, "schema": self.schema}


@dataclass
class CallInfo:
    run_id: str
    node_id: str
    ordinal: int  # nth call of this tool by this node in the run
    attempt: int
    idempotency_key: str | None
    approval_id: str | None
    cancelled: Callable[[], bool]


def validate_args(schema: dict, args: Any) -> str | None:
    if not isinstance(args, dict):
        return "arguments must be an object"
    for r in schema.get("required", []):
        if r not in args or args[r] in (None, ""):
            return f"missing required argument '{r}'"
    for k, spec in schema.get("properties", {}).items():
        if k in args and args[k] is not None:
            t = spec.get("type")
            ok = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}.get(t)
            if ok and (not isinstance(args[k], ok) or (t in ("integer", "number") and isinstance(args[k], bool))):
                return f"argument '{k}' must be of type {t}"
    return None
