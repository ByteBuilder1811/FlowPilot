"""Guarded invocation of tools and LLMs: checkpointing, approval gate, fault injection, fixtures, retry policy, reconciliation."""
from backend import config
from backend.db.models import ApprovalRequest, ToolCall
from backend.db.session import new_id, now, session
from backend.execution import store
from backend.execution.context import Exec, FixtureMiss, NodeFailed, Step, Suspend, canon, sha
from backend.execution.state import TERMINAL
from backend.providers.base import LLMResult, ProviderError
from backend.providers.registry import get_provider
from backend.security.redaction import redact
from backend.tools.base import CallInfo, ToolError, validate_args
from backend.tools.builtin import REGISTRY

SAFE_METHODS = ("GET", "HEAD")


def is_side_effect(tool: str, args: dict) -> bool:
    spec = REGISTRY.get(tool)
    if not spec:
        return False
    if tool == "http.request":
        return str(args.get("method", "GET")).upper() not in SAFE_METHODS
    return spec.side_effect


def resolve_policy(ex: Exec, node: dict) -> dict:
    cfg = dict((node.get("data", {}).get("config", {}).get("retry")) or {})
    if not cfg:  # a Retry/Recovery Policy node wired directly in front of this node applies to it
        for src in ex.in_edges.get(node["id"], []):
            sn = ex.nodes[src]
            if sn["type"] == "retry":
                cfg = dict(sn["data"].get("config", {}))
    return {
        "max_attempts": min(int(cfg.get("max_attempts", 1)), config.HARD_MAX_RETRIES),
        "backoff_ms": int(cfg.get("backoff_ms", 500)),
        "max_backoff_ms": min(int(cfg.get("max_backoff_ms", 8000)), config.HARD_MAX_BACKOFF_MS),
        "safe_to_retry": bool(cfg.get("safe_to_retry", False)),
    }


# ----------------------------------------------------------------- approvals
def _find_approval(run_id, node_id, visit, kind, args_hash):
    with session() as s:
        return s.query(ApprovalRequest).filter_by(run_id=run_id, node_id=node_id, visit=visit, kind=kind, args_hash=args_hash).first()


def _create_approval(ex: Exec, step: Step, kind: str, proposed: dict, args_hash: str) -> str:
    aid = new_id("apr")
    status, decision, decided = "pending", None, None
    if ex.mode == "fixture_replay":  # replays use the decisions recorded with the case; default is the safe one: reject
        rec = next((a for a in ex.options.get("approvals", []) if a.get("node_id") == step.node_id and a.get("kind", "gate") == kind), None)
        status = (rec or {}).get("status") or ("approved" if ex.options.get("approval_default", "approve") == "approve" else "rejected")
        decision = {"replayed": True, "note": "decision replayed from recorded case" if rec else f"no recorded decision: case default '{ex.options.get('approval_default', 'approve')}' applied (replay executes nothing real)", **({k: rec[k] for k in ("action", "output") if rec and k in rec})}
        decided = now()
    with session() as s:
        s.add(ApprovalRequest(id=aid, run_id=ex.run_id, node_id=step.node_id, visit=step.visit, kind=kind, status=status,
                              proposed=redact(proposed), args_hash=args_hash, decision=decision, created_at=now(), decided_at=decided))
    ex.emit("approval.requested" if status == "pending" else "approval.replayed", {"approval_id": aid, "kind": kind, "status": status, "proposed": proposed})
    return aid


def gate(ex: Exec, step: Step, kind: str, proposed: dict, args_hash: str) -> ApprovalRequest:
    """Return an *approved* request or raise Suspend / NodeFailed. Enforced here, in the backend."""
    ap = _find_approval(ex.run_id, step.node_id, step.visit, kind, args_hash)
    if ap is None:
        _create_approval(ex, step, kind, proposed, args_hash)
        ap = _find_approval(ex.run_id, step.node_id, step.visit, kind, args_hash)
    if ap.status == "pending":
        raise Suspend(ap.id)
    return ap


def verify_approved(approval_id: str | None, run_id: str, args_hash: str) -> bool:
    if not approval_id:
        return False
    with session() as s:
        ap = s.get(ApprovalRequest, approval_id)
        return bool(ap and ap.run_id == run_id and ap.status == "approved" and ap.args_hash == args_hash and ap.kind == "gate")


# ----------------------------------------------------------------- persistence
def _row(run_id, node_id, visit, seq, kind):
    with session() as s:
        return s.query(ToolCall).filter_by(run_id=run_id, node_id=node_id, visit=visit, seq=seq, kind=kind).first()


def _insert(ex, step, kind, tool, args, seq, attempt, side, key, approval_id, source="live") -> str:
    cid = new_id("tc")
    with session() as s:
        ordinal = s.query(ToolCall).filter_by(run_id=ex.run_id, node_id=step.node_id, kind=kind, tool=tool).count()
        s.add(ToolCall(id=cid, run_id=ex.run_id, node_id=step.node_id, kind=kind, tool=tool, visit=step.visit, seq=seq, ordinal=ordinal, attempt=attempt,
                       args=redact(args), args_hash=sha(args), outcome="started", side_effect=side, idempotency_key=key, approval_id=approval_id,
                       source=source, started_at=now()))
    return cid


def _finish(cid, outcome, result=None, error=None, source=None):
    with session() as s:
        r = s.get(ToolCall, cid)
        r.outcome, r.ended_at = outcome, now()
        r.result = redact(result) if result is not None else None
        r.error = error
        if source:
            r.source = source


def _ordinal(cid):
    with session() as s:
        return s.get(ToolCall, cid).ordinal


def _fault_for(ex: Exec, node_id: str, tool: str, ordinal: int):
    for f in ex.faults:
        if f.get("tool") not in (None, "", tool) or f.get("node_id") not in (None, "", node_id):
            continue
        start = int(f.get("start", 0))
        if start <= ordinal < start + int(f.get("times", 1)):
            return f
    return None


def _fixture_for(ex: Exec, node_id: str, kind: str, tool: str, ordinal: int):
    return next((f for f in ex.fixtures if f["node_id"] == node_id and f["kind"] == kind and f["tool"] == tool and f["ordinal"] == ordinal), None)


FAULTS = {
    "timeout": lambda se: ToolError("timeout", "injected fault: timeout", True, None if se else False),
    "rate_limit": lambda se: ToolError("rate_limit", "injected fault: rate limited (429)", True, False),
    "transient_error": lambda se: ToolError("unavailable", "injected fault: service unavailable (503)", True, False),
    "malformed_response": lambda se: ToolError("malformed_response", "injected fault: malformed response", False, None if se else False),
}


# ----------------------------------------------------------------- retry decision
def decide_retry(err_kind: str, transient: bool, executed, attempt: int, pol: dict, side_effect: bool, has_key: bool) -> tuple[str, str]:
    """-> (decision, reason). decision: retry | final | reconcile"""
    left = attempt < pol["max_attempts"]
    if side_effect and executed is None:  # the action may have happened: never fail silently, never repeat blindly
        if transient and left and has_key:
            return "retry", "outcome unknown but an idempotency key makes the retry safe"
        if transient and left and pol["safe_to_retry"]:
            return "retry", "outcome unknown; retried because the node policy explicitly sets safe_to_retry"
        return "reconcile", "outcome of a non-idempotent side effect is unknown; refusing to repeat it automatically"
    if not transient:
        return "final", f"permanent error ({err_kind}); not retried"
    if not left:
        return "final", f"retry budget exhausted ({attempt}/{pol['max_attempts']} attempts)" if pol["max_attempts"] > 1 else "retry policy allows a single attempt"
    return "retry", "transient error; the call did not take effect" if side_effect else "transient error on a read-only call"


def _backoff(ex: Exec, step: Step, pol: dict, attempt: int, reason: str, tool: str):
    ms = min(pol["backoff_ms"] * (2 ** (attempt - 1)), pol["max_backoff_ms"])
    ex.stats["retries"] += 1
    ex.emit("retry.scheduled", {"tool": tool, "failed_attempt": attempt, "next_attempt": attempt + 1, "backoff_ms": ms, "reason": reason,
                                "skipped_wait": ex.mode == "fixture_replay"})
    store.transition(ex.run_id, "retrying", quiet=True)
    try:
        ex.sleep(ms / 1000)
    finally:
        pass
    store.transition(ex.run_id, "running", quiet=True)


def _reconcile(ex: Exec, step: Step, tool: str, args: dict, seq: int, err: ToolError):
    h = sha({"reconcile": tool, "args": args, "seq": step.node_id + str(step.visit) + str(seq)})
    ap = _find_approval(ex.run_id, step.node_id, step.visit, "reconcile", h)
    if ap is None:
        _create_approval(ex, step, "reconcile", {"tool": tool, "args": redact(args), "problem": err.message, "options": ["retry", "mark_done", "fail"],
                                                 "consequence": "The remote system may already have performed this action. Retrying could duplicate it."}, h)
        ap = _find_approval(ex.run_id, step.node_id, step.visit, "reconcile", h)
    if ap.status == "pending":
        raise Suspend(ap.id)
    action = (ap.decision or {}).get("action", "fail") if ap.status == "approved" else "fail"
    return action, (ap.decision or {}).get("output")


# ----------------------------------------------------------------- tool invocation
def invoke_tool(ex: Exec, step: Step, tool: str, args: dict, *, pol: dict, key: str | None = None, force_gate: bool = False) -> dict:
    spec = REGISTRY.get(tool)
    if spec is None:
        raise NodeFailed("unknown_tool", f"Tool '{tool}' does not exist")
    bad = validate_args(spec.schema, args)
    if bad:
        raise NodeFailed("invalid_arguments", f"{tool}: {bad}")
    side = is_side_effect(tool, args)
    if key and not spec.supports_idempotency_key:
        key = None
    args_hash = sha({"tool": tool, "args": args})
    approval_id = None
    if side and (ex.require_gate or force_gate):
        proposed = {"tool": tool, "target": args.get("to") or args.get("url") or args.get("customer"), "arguments": redact(args),
                    "side_effect": True, "consequence": f"Executes '{tool}', which changes state in an external system.",
                    "idempotency_key": key}
        ap = gate(ex, step, "gate", proposed, args_hash)
        if ap.status == "rejected":
            raise NodeFailed("approval_rejected", f"A human rejected '{tool}'. The action was not executed.", {"approval_id": ap.id})
        approval_id = ap.id

    attempt = 0
    while True:
        attempt += 1
        ex.check()
        seq = step.next_seq()
        existing = _row(ex.run_id, step.node_id, step.visit, seq, "tool")
        err: ToolError | None = None
        result = None
        if existing and existing.outcome == "ok":
            result = existing.result  # step-internal checkpoint: never re-execute a recorded success
        elif existing and existing.outcome in ("error", "unknown") and existing.error:
            err = ToolError(**existing.error)
        elif existing:  # 'started': crashed mid-call
            err = ToolError("interrupted", "FlowPilot stopped while this call was in flight; the outcome is unknown", True, None if side else False)
            _finish(existing.id, "unknown", error=err.to_dict())
        else:
            cid = _insert(ex, step, "tool", tool, args, seq, attempt, side, key, approval_id)
            ordinal = _ordinal(cid)
            fixture = _fixture_for(ex, step.node_id, "tool", tool, ordinal) if ex.mode == "fixture_replay" else None
            fault = _fault_for(ex, step.node_id, tool, ordinal)
            ex.stats["tool_calls"] += 1
            ex.emit("tool.called", {"tool": tool, "args": args, "attempt": attempt, "side_effect": side, "idempotency_key": key,
                                    "approval_id": approval_id, "mode": ex.mode})
            try:
                if fault:
                    raise FAULTS[fault["kind"]](side)
                if ex.mode == "fixture_replay":
                    if fixture:
                        if fixture["outcome"] == "ok":
                            result = fixture["result"]
                            _finish(cid, "ok", result, source="fixture")
                        else:
                            raise ToolError(**fixture["error"])
                    elif side:  # replays never perform real side effects: simulate, clearly labelled
                        result = {"simulated": True, "note": "no fixture recorded; side effect NOT executed (fixture replay)"}
                        _finish(cid, "ok", result, source="simulated")
                    elif spec.replay_safe:
                        result = spec.fn(CallInfo(ex.run_id, step.node_id, ordinal, attempt, key, approval_id, lambda: False), args)
                        _finish(cid, "ok", result, source="deterministic")
                    else:
                        _finish(cid, "error", error={"kind": "fixture_miss", "message": "no fixture recorded for this call", "transient": False, "executed": False})
                        raise FixtureMiss(f"{tool} (node {step.node_id}, call #{ordinal})")
                else:
                    if side and not verify_approved(approval_id, ex.run_id, args_hash) and (ex.require_gate or force_gate):
                        raise ToolError("forbidden", "approval missing at execution time; refusing to execute", False, False)
                    result = spec.fn(CallInfo(ex.run_id, step.node_id, ordinal, attempt, key, approval_id, lambda: False), args)
                    if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
                        raise ToolError("malformed_response", "tool returned a non-JSON value", False, None if side else False)
                    _finish(cid, "ok", result)
            except ToolError as te:
                err = te
                if fault:
                    _finish(cid, "error", error=te.to_dict(), source="fault")
                elif ex.mode != "fixture_replay" or not fixture:
                    _finish(cid, "error" if te.executed is not None else "unknown", error=te.to_dict())
                else:
                    _finish(cid, "error", error=te.to_dict(), source="fixture")
        if err is None:
            ex.emit("tool.succeeded", {"tool": tool, "attempt": attempt, "result": result})
            return result
        ex.emit("tool.failed", {"tool": tool, "attempt": attempt, "error": err.to_dict()})
        step.attempts.append({"n": attempt, "tool": tool, "status": "failed", "error": err.to_dict()})
        decision, reason = decide_retry(err.kind, err.transient, err.executed, attempt, pol, side, bool(key))
        step.attempts[-1]["decision"], step.attempts[-1]["reason"] = decision, reason
        if decision == "retry":
            _backoff(ex, step, pol, attempt, reason, tool)
            continue
        if decision == "reconcile":
            ex.emit("retry.blocked", {"tool": tool, "attempt": attempt, "reason": reason})
            action, out = _reconcile(ex, step, tool, args, seq, err)
            ex.emit("reconcile.decided", {"tool": tool, "action": action})
            step.attempts[-1]["reconciliation"] = action
            if action == "retry":
                _backoff(ex, step, {**pol, "backoff_ms": 0}, attempt, "operator chose to retry after reconciliation", tool)
                continue
            if action == "mark_done":
                with session() as s:
                    s.query(ToolCall).filter_by(run_id=ex.run_id, node_id=step.node_id, visit=step.visit, seq=seq, kind="tool").update(
                        {"outcome": "ok", "result": redact(out if isinstance(out, dict) else {"reconciled": True, "value": out}), "source": "reconciled"})
                return out if isinstance(out, dict) else {"reconciled": True, "value": out}
            raise NodeFailed("reconciliation_failed", f"{tool}: outcome unknown and the operator chose to fail the run", err.to_dict())
        ex.emit("retry.declined", {"tool": tool, "attempt": attempt, "reason": reason})
        raise NodeFailed(f"tool_{err.kind}", f"{tool}: {err.message}", {**err.to_dict(), "attempts": attempt, "retry_reason": reason})


# ----------------------------------------------------------------- LLM invocation
def invoke_llm(ex: Exec, step: Step, *, provider: str, model: str, system: str, messages: list, tools: list, pol: dict, timeout: float) -> LLMResult:
    attempt = 0
    while True:
        attempt += 1
        ex.check()
        seq = step.next_seq()
        existing = _row(ex.run_id, step.node_id, step.visit, seq, "llm")
        err = None
        res = None
        if existing and existing.outcome == "ok":
            return LLMResult.from_dict(existing.result)
        if existing and existing.outcome == "error":
            err = ProviderError(**existing.error)
        else:
            if existing:
                err = ProviderError("interrupted", "interrupted during an LLM call", True)
                _finish(existing.id, "error", error=err.to_dict())
            else:
                args = {"provider": provider, "model": model, "system": system, "messages": messages, "tools": [t["name"] for t in tools]}
                cid = _insert(ex, step, "llm", "llm.complete", args, seq, attempt, False, None, None)
                ordinal = _ordinal(cid)
                fixture = _fixture_for(ex, step.node_id, "llm", "llm.complete", ordinal) if ex.mode == "fixture_replay" else None
                fault = _fault_for(ex, step.node_id, "llm.complete", ordinal)
                ex.stats["llm_calls"] += 1
                ex.emit("llm.requested", {"provider": provider, "model": model, "attempt": attempt, "message_count": len(messages)})
                try:
                    if fault:
                        te = FAULTS[fault["kind"]](False)
                        raise ProviderError(te.kind, te.message, te.transient)
                    if fixture:
                        if fixture["outcome"] == "ok":
                            res = LLMResult.from_dict(fixture["result"])
                            _finish(cid, "ok", res.to_dict(), source="fixture")
                        else:
                            raise ProviderError(**fixture["error"])
                    elif ex.mode == "fixture_replay" and provider != "mock":
                        _finish(cid, "error", error={"kind": "fixture_miss", "message": "no LLM fixture recorded", "transient": False})
                        raise FixtureMiss(f"llm.complete (node {step.node_id}, call #{ordinal})")
                    else:
                        res = get_provider(provider).complete(model=model, system=system, messages=messages, tools=tools, timeout=timeout)
                        _finish(cid, "ok", res.to_dict(), source="live")
                except ProviderError as pe:
                    err = pe
                    _finish(cid, "error", error=pe.to_dict(), source="fault" if fault else None)
        if err is None:
            if res.usage:
                ex.stats["tokens_reported"] = True
                ex.stats["input_tokens"] += res.usage.get("input_tokens") or 0
                ex.stats["output_tokens"] += res.usage.get("output_tokens") or 0
            ex.emit("llm.responded", {"provider": res.provider, "model": res.model, "mock": res.mock, "text": res.text, "tool_calls": res.tool_calls, "usage": res.usage})
            return res
        ex.emit("llm.failed", {"attempt": attempt, "error": err.to_dict()})
        step.attempts.append({"n": attempt, "tool": "llm.complete", "status": "failed", "error": err.to_dict()})
        decision, reason = decide_retry(err.kind, err.transient, False, attempt, pol, False, False)
        step.attempts[-1]["decision"], step.attempts[-1]["reason"] = decision, reason
        if decision == "retry":
            _backoff(ex, step, pol, attempt, reason, "llm.complete")
            continue
        ex.emit("retry.declined", {"tool": "llm.complete", "attempt": attempt, "reason": reason})
        raise NodeFailed(f"llm_{err.kind}", err.message, {**err.to_dict(), "attempts": attempt, "retry_reason": reason})
