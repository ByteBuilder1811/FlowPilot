"""Secret redaction applied before anything is persisted or returned by the API."""
import re
from typing import Any

SENSITIVE_KEY = re.compile(r"(authorization|api[_-]?key|apikey|password|passwd|secret|token|cookie|credential|private[_-]?key|x-api)", re.I)
SENSITIVE_VALUE = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
]
REDACTED = "[REDACTED]"
MAX_STR = 20_000


def redact_text(s: str) -> str:
    for rx in SENSITIVE_VALUE:
        s = rx.sub(REDACTED, s)
    return s if len(s) <= MAX_STR else s[:MAX_STR] + f"…[truncated {len(s) - MAX_STR} chars]"


def redact(obj: Any, _depth: int = 0) -> Any:
    """Return a deep-copied structure with sensitive keys/values masked."""
    if _depth > 12:
        return "[max-depth]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = REDACTED if isinstance(k, str) and SENSITIVE_KEY.search(k) and v not in (None, "", False) else redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj[:500]]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
