"""Node handlers. Each returns (output, handle, ctx_patch). Handlers must be deterministic given recorded call results."""
import json
import re

from backend import config
from backend.execution import runner
from backend.execution.context import Exec, FixtureMiss, NodeFailed, Step, Suspend, SuspendForChildren, sha
from backend.tools.builtin import REGISTRY
from backend.security.redaction import redact
from backend.workflows.expr import ExprError, evaluate, render


def _cfg(step: Step) -> dict:
    return step.node.get("data", {}).get("config", {})


def _render(ex: Exec, value):
    try:
        return render(value, ex.env())
    except ExprError as e:
        raise NodeFailed("template_error", str(e)) from e


def h_start(ex, step):
    schema = _cfg(step).get("input_schema") or {}
    inp = ex.ctx["input"]
    for r in schema.get("required", []):
        if r not in inp:
            raise NodeFailed("invalid_input", f"Missing required input field '{r}'")
    for k, spec in (schema.get("properties") or {}).items():
        t = spec.get("type")
        if k in inp and t:
            ok = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list}.get(t)
            if ok and (not isinstance(inp[k], ok) or (t in ("number", "integer") and isinstance(inp[k], bool))):
                raise NodeFailed("invalid_input", f"Input field '{k}' must be of type {t}")
    step.input = inp
    return inp, "out", None


def h_end(ex, step):
    out = _render(ex, _cfg(step).get("output", "{{ input }}"))
    step.input = None
    return {"result": out}, None, None


def _tool_call(ex, step, tool, args):
    cfg = _cfg(step)
    key = _render(ex, cfg.get("idempotency_key")) if cfg.get("idempotency_key") else None
    step.input = {"tool": tool, "args": args}
    return runner.invoke_tool(ex, step, tool, args, pol=runner.resolve_policy(ex, step.node), key=str(key) if key else None,
                              force_gate=bool(cfg.get("requires_approval")))


def h_tool(ex, step):
    cfg = _cfg(step)
    args = _render(ex, cfg.get("args") or {})
    return _tool_call(ex, step, cfg["tool"], args), "out", None


def h_http(ex, step):
    cfg = _cfg(step)
    args = _render(ex, {k: cfg.get(k) for k in ("url", "method", "headers", "body", "timeout_s") if cfg.get(k) is not None})
    return _tool_call(ex, step, "http.request", args), "out", None


def h_transform(ex, step):
    cfg = _cfg(step)
    op = cfg["op"]
    step.input = {"op": op}
    if op == "template":
        out = _render(ex, cfg["template"])
        return {"value": out}, "out", None
    if op == "expression":
        try:
            return {"value": evaluate(cfg["expression"], ex.env())}, "out", None
        except ExprError as e:
            raise NodeFailed("expression_error", str(e)) from e
    if op == "pick":
        src = _render(ex, cfg.get("source", "{{ input }}"))
        keys = cfg.get("keys") or []
        return {"value": {k: (src or {}).get(k) for k in keys} if isinstance(src, dict) else None}, "out", None
    if op == "json_parse":
        src = _render(ex, cfg.get("source", ""))
        try:
            return {"value": json.loads(src) if isinstance(src, str) else src}, "out", None
        except ValueError as e:
            raise NodeFailed("json_parse_error", f"Invalid JSON: {e}") from e
    if op == "regex_extract":
        src = str(_render(ex, cfg.get("source", "")))
        pattern = str(cfg.get("pattern", ""))
        if len(pattern) > 200 or len(src) > 100_000:
            raise NodeFailed("regex_limit", "pattern or input too large")
        try:
            m = re.search(pattern, src)
        except re.error as e:
            raise NodeFailed("regex_error", str(e)) from e
        return {"value": (m.group(1) if m and m.groups() else (m.group(0) if m else None)), "matched": bool(m)}, "out", None
    if op == "merge":
        parts = _render(ex, cfg.get("sources") or [])
        merged = {}
        for p in parts:
            if isinstance(p, dict):
                merged.update(p)
        return {"value": merged}, "out", None
    raise NodeFailed("unsupported_operation", f"Unsupported transform '{op}'")


def h_set_variable(ex, step):
    cfg = _cfg(step)
    val = _render(ex, cfg.get("value"))
    step.input = {"name": cfg["name"]}
    return {"name": cfg["name"], "value": val}, "out", {"vars": {cfg["name"]: val}}


def h_condition(ex, step):
    expr = _cfg(step)["expression"]
    try:
        v = bool(evaluate(expr, ex.env()))
    except ExprError as e:
        raise NodeFailed("expression_error", str(e)) from e
    step.input = {"expression": expr}
    return {"result": v}, "true" if v else "false", None


def h_loop(ex, step):
    cfg = _cfg(step)
    nid = step.node_id
    mx = min(int(cfg["max_iterations"]), config.HARD_MAX_LOOP_ITERATIONS)
    st = ex.ctx["loop"].get(nid)
    if st is None:
        if cfg.get("mode", "count") == "foreach":
            try:
                items = evaluate(cfg["items"], ex.env())
            except ExprError as e:
                raise NodeFailed("expression_error", str(e)) from e
            if not isinstance(items, list):
                raise NodeFailed("loop_items_invalid", "'items' must evaluate to a list")
        else:
            n = _render(ex, cfg.get("count", 1))
            items = list(range(int(n)))
        if len(items) > mx:
            if cfg.get("on_limit", "fail") == "truncate":
                items = items[:mx]
            else:
                raise NodeFailed("loop_limit_exceeded", f"Loop would run {len(items)} iterations but max_iterations is {mx}",
                                 {"requested": len(items), "max_iterations": mx})
        st = {"i": 0, "items": items}
    step.input = {"index": st["i"], "total": len(st["items"]), "max_iterations": mx}
    if st["i"] < len(st["items"]):
        item = st["items"][st["i"]]
        new = {"i": st["i"] + 1, "items": st["items"]}
        return {"item": item, "index": st["i"], "total": len(st["items"])}, "body", {"loop": {nid: new}}
    return {"completed": st["i"]}, "done", {"loop": {nid: None}}  # reset so a nested re-entry starts fresh


def h_delay(ex, step):
    secs = float(_cfg(step).get("seconds", 1))
    step.input = {"seconds": secs}
    ex.emit("delay.started", {"seconds": secs, "skipped": ex.mode == "fixture_replay"})
    ex.sleep(secs)
    return {"waited_seconds": 0 if ex.mode == "fixture_replay" else secs}, "out", None


def h_retry(ex, step):
    cfg = _cfg(step)
    step.input = None
    return {"policy": {k: cfg.get(k) for k in ("max_attempts", "backoff_ms", "max_backoff_ms", "safe_to_retry")}}, "out", None


def h_approval(ex, step):
    cfg = _cfg(step)
    proposed = _render(ex, {"title": cfg.get("title", "Approval required"), "message": cfg.get("message", ""), "details": cfg.get("details", {})})
    h = sha({"node": step.node_id, "p": proposed})
    ap = runner.gate(ex, step, "node", proposed, h)
    step.input = proposed
    if ap.status == "approved":
        return {"decision": "approved", "note": (ap.decision or {}).get("note")}, "approved", None
    if (step.node_id, "rejected") in ex.out:
        return {"decision": "rejected", "note": (ap.decision or {}).get("note")}, "rejected", None
    raise NodeFailed("approval_rejected", "A human rejected this step and no 'rejected' path is connected.", {"approval_id": ap.id})


def h_llm(ex, step):
    cfg = dict(_cfg(step))
    snap = cfg.get("agent_snapshot") or {}
    for k, v in snap.items():
        cfg.setdefault(k, v)
    provider, model = cfg.get("provider", "mock"), cfg.get("model") or "mock-agent-1"
    system = _render(ex, cfg.get("system", ""))
    prompt = _render(ex, cfg["prompt"])
    if not isinstance(prompt, str):
        prompt = json.dumps(prompt, default=str)
    tool_names = [t for t in (cfg.get("tools") or []) if t in REGISTRY]
    tools = [{"name": n, "description": REGISTRY[n].description, "schema": REGISTRY[n].schema} for n in tool_names]
    max_it = min(int(cfg.get("max_iterations", ex.settings["max_agent_iterations"])), config.HARD_MAX_AGENT_ITERATIONS)
    timeout = min(float(cfg.get("timeout_s", 60)), 300)
    pol = runner.resolve_policy(ex, step.node)
    step.input = {"provider": provider, "model": model, "system": system, "prompt": prompt, "tools": tool_names}
    mem = cfg.get("memory") or {}
    mem_scope = mem_key = None
    history: list[dict] = []
    if mem.get("enabled"):
        mem_scope = str(cfg.get("agent_id") or f"{ex.workflow_id}:{step.node_id}")[:80]
        mem_key = str(_render(ex, mem.get("key") or "{{ run.id }}"))[:200]
        if ex.mode == "fixture_replay":
            ex.emit("memory.skipped_in_replay", {"scope": mem_scope})  # replays are deterministic: no memory reads or writes
        else:
            history = memory_load(mem_scope, mem_key, min(int(mem.get("window", 6)), 20))
            ex.emit("memory.loaded", {"scope": mem_scope, "key": mem_key, "messages": len(history)})
    messages = history + [{"role": "user", "content": prompt}]
    thr = int(ex.settings["loop_repeat_threshold"])
    trace = []
    for it in range(1, max_it + 1):
        res = runner.invoke_llm(ex, step, provider=provider, model=model, system=system, messages=messages, tools=tools, pol=pol, timeout=timeout)
        if not res.tool_calls:
            out = {"text": res.text, "iterations": it, "provider": res.provider, "model": res.model, "mock": res.mock, "tool_trace": trace,
                   "usage": res.usage}
            if cfg.get("output_format") == "json":
                try:
                    out["json"] = json.loads(res.text)
                except ValueError:
                    raise NodeFailed("malformed_model_output", "Model output was not valid JSON", {"text": res.text[:500]}) from None
            if mem_scope and ex.mode != "fixture_replay":
                memory_save(mem_scope, mem_key, ex.run_id, step.node_id, step.visit, prompt, res.text)
            return out, "out", None
        blocks = ([{"type": "text", "text": res.text}] if res.text else []) + [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in res.tool_calls]
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for c in res.tool_calls:
            sig = f"{step.node_id}:{step.visit}:{c['name']}:{sha(c['input'])}"
            ex.sig_counts[sig] = ex.sig_counts.get(sig, 0) + 1
            if ex.sig_counts[sig] >= thr:
                ex.emit("loop.detected", {"signal": "repeated_tool_call", "tool": c["name"], "repeats": ex.sig_counts[sig], "iteration": it, "threshold": thr})
                raise NodeFailed("loop_detected", f"Agent repeated the identical call to '{c['name']}' {ex.sig_counts[sig]} times (threshold {thr}).",
                                 {"signal": "repeated_tool_call", "tool": c["name"], "args": c["input"], "iteration": it, "iteration_budget": max_it})
            if c["name"] not in tool_names:
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps({"error": f"tool '{c['name']}' is not permitted for this agent"}), "is_error": True})
                trace.append({"tool": c["name"], "denied": True})
                continue
            try:
                r = runner.invoke_tool(ex, step, c["name"], c["input"], pol=pol, force_gate=bool(cfg.get("requires_approval")))
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(r, default=str)})
                trace.append({"tool": c["name"], "args": c["input"], "ok": True})
            except NodeFailed as nf:
                if cfg.get("tool_error", "fail") == "report_to_model" and nf.code != "approval_rejected":
                    results.append({"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps({"error": nf.message}), "is_error": True})
                    trace.append({"tool": c["name"], "args": c["input"], "ok": False, "error": nf.message})
                else:
                    raise
        messages.append({"role": "user", "content": results})
    ex.emit("loop.detected", {"signal": "iteration_budget", "iterations": max_it})
    raise NodeFailed("agent_iteration_limit", f"Agent did not finish within {max_it} iterations", {"iteration_budget": max_it, "tool_trace": trace})


def memory_load(scope: str, key: str, window: int) -> list[dict]:
    from backend.db.models import AgentMemory
    from backend.db.session import session
    with session() as s:
        rows = s.query(AgentMemory).filter_by(scope=scope, key=key).order_by(AgentMemory.id.desc()).limit(max(1, window) * 2).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def memory_save(scope: str, key: str, run_id: str, node_id: str, visit: int, user: str, assistant: str) -> None:
    """Idempotent per (run, node, visit, role): a resumed or re-driven step never double-writes."""
    from sqlalchemy.exc import IntegrityError

    from backend.db.models import AgentMemory
    from backend.db.session import now, session
    for role, content in (("user", user), ("assistant", assistant)):
        try:
            with session() as s:
                s.add(AgentMemory(scope=scope, key=key, role=role, content=redact(content)[:8000], run_id=run_id, node_id=node_id, visit=visit, created_at=now()))
        except IntegrityError:
            pass


MAX_DEPTH = 3
MAX_CHILDREN = 20


def h_subworkflow(ex, step):
    """Call another workflow as child run(s). 'map' mode fans out one child per item, running in parallel on the worker pool."""
    from backend.db.models import Run, ToolCall, Workflow
    from backend.db.session import new_id, now, session
    from backend.execution import store
    from backend.workflows import service

    cfg = _cfg(step)
    target = cfg["workflow_id"]
    foreach = cfg.get("mode", "single") == "foreach"
    with session() as s:
        me = s.get(Run, ex.run_id)
        depth, my_wf = me.depth or 0, me.workflow_id
        chain, cur = {my_wf}, me.parent_run_id
        for _ in range(MAX_DEPTH + 2):
            if not cur:
                break
            p = s.get(Run, cur)
            if not p:
                break
            chain.add(p.workflow_id)
            cur = p.parent_run_id
        tw = s.get(Workflow, target)
    if not tw or tw.deleted:
        raise NodeFailed("subworkflow_missing", "The called workflow does not exist")
    if target in chain:
        raise NodeFailed("subworkflow_cycle", "A workflow may not (indirectly) call itself")
    if depth + 1 > MAX_DEPTH:
        raise NodeFailed("subworkflow_depth", f"Sub-workflow nesting is limited to {MAX_DEPTH} levels")
    if foreach:
        try:
            items = evaluate(cfg["items"], ex.env())
        except ExprError as e:
            raise NodeFailed("expression_error", str(e)) from e
        if not isinstance(items, list):
            raise NodeFailed("map_items_invalid", "'items' must evaluate to a list")
        cap = min(int(cfg.get("max_items", 10)), MAX_CHILDREN)
        if len(items) > cap:
            raise NodeFailed("map_limit_exceeded", f"Map would start {len(items)} child runs but max_items is {cap}", {"requested": len(items), "max_items": cap})
    else:
        items = [None]
    step.input = {"workflow_id": target, "mode": "map" if foreach else "single", "items": len(items) if foreach else None}

    def build(rows):
        res = [{"index": i, "run_id": r["run_id"], "status": r["status"], "result": (r["output"] or {}).get("result") if r["status"] == "succeeded" else None, "error": r["error"]} for i, r in enumerate(rows)]
        if not foreach:
            r0 = res[0]
            if r0["status"] != "succeeded":
                raise NodeFailed("subworkflow_failed", f"Child run {r0['status']}: {(r0['error'] or {}).get('message', '')}", {"child_run_id": r0["run_id"], "child_error": r0["error"]})
            return {"result": r0["result"], "run_id": r0["run_id"]}
        failed = [x for x in res if x["status"] != "succeeded"]
        if failed and cfg.get("on_item_error", "fail") == "fail":
            raise NodeFailed("map_item_failed", f"{len(failed)} of {len(res)} child runs did not succeed", {"failed_indexes": [x["index"] for x in failed], "first_error": failed[0]["error"]})
        return {"results": res, "failed": len(failed), "succeeded": len(res) - len(failed)}

    if ex.mode == "fixture_replay":  # replays never start real child runs
        fx = next((f for f in ex.fixtures if f["node_id"] == step.node_id and f["tool"] == "subworkflow.call" and f["ordinal"] == 0), None)
        if not fx:
            raise FixtureMiss(f"subworkflow.call (node {step.node_id})")
        if fx["outcome"] != "ok":
            raise NodeFailed("subworkflow_failed", "recorded child failure", fx.get("error") or {})
        return fx["result"], "out", None

    prefix = f"{step.node_id}:{step.visit}:"
    ver = service.snapshot_version(target, "Snapshot for sub-workflow call")["id"]
    with session() as s:
        existing = {r.parent_key: r.id for r in s.query(Run).filter(Run.parent_run_id == ex.run_id).all() if (r.parent_key or "").startswith(prefix)}
    for i, it in enumerate(items):
        key = f"{prefix}{i}"
        if key in existing:
            continue
        env = {**ex.env(), "item": it, "index": i}
        try:
            child_in = render(cfg.get("input") if cfg.get("input") is not None else {}, env)
        except ExprError as e:
            raise NodeFailed("template_error", str(e)) from e
        if not isinstance(child_in, dict):
            raise NodeFailed("subworkflow_input", "Sub-workflow input must render to an object")
        cid = store.create_run(target, ver, child_in, trigger="subworkflow", parent_run_id=ex.run_id, parent_key=key, depth=depth + 1)
        ex.emit("child.started", {"child_run_id": cid, "index": i, "workflow_id": target})
    with session() as s:
        kids = [r for r in s.query(Run).filter(Run.parent_run_id == ex.run_id).all() if (r.parent_key or "").startswith(prefix)]
        kids.sort(key=lambda r: int(r.parent_key.rsplit(":", 1)[1]))
        rows = [{"run_id": r.id, "status": r.status, "output": r.output, "error": r.error} for r in kids]
    if any(r["status"] not in ("succeeded", "failed", "cancelled", "timed_out") for r in rows):
        raise SuspendForChildren()
    ex.emit("child.finished", {"children": len(rows), "failed": sum(1 for r in rows if r["status"] != "succeeded")})
    try:
        out = build(rows)
    except NodeFailed:
        raise
    finally:  # record for fixture replay either way
        with session() as s:
            n = s.query(ToolCall).filter_by(run_id=ex.run_id, node_id=step.node_id, tool="subworkflow.call").count()
            if n == 0:
                ok = all(r["status"] == "succeeded" for r in rows) or (foreach and cfg.get("on_item_error", "fail") != "fail")
                s.add(ToolCall(id=new_id("tc"), run_id=ex.run_id, node_id=step.node_id, kind="tool", tool="subworkflow.call", visit=step.visit, seq=0, ordinal=0, attempt=1,
                               args={}, args_hash="", outcome="ok" if ok else "error", result=redact(build_safe(build, rows)) if ok else None,
                               error=None if ok else {"kind": "subworkflow_failed", "message": "child run did not succeed", "transient": False, "executed": None},
                               side_effect=False, source="live", started_at=now(), ended_at=now()))
    return out, "out", None


def build_safe(build, rows):
    try:
        return build(rows)
    except NodeFailed:
        return None


HANDLERS = {"subworkflow": h_subworkflow,"start": h_start, "end": h_end, "tool": h_tool, "http": h_http, "transform": h_transform, "set_variable": h_set_variable,
            "condition": h_condition, "loop": h_loop, "delay": h_delay, "retry": h_retry, "approval": h_approval, "llm": h_llm}
