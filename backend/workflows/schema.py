"""Workflow definition format (React-Flow compatible) and static validation.

Definition: {"nodes":[{id,type,position:{x,y},data:{label,config}}], "edges":[{id,source,target,sourceHandle}],
             "policy":{max_steps,max_run_seconds}, "format":"flowpilot.workflow/v1"}
"""
import hashlib
import json
from typing import Any

from backend import config
from backend.workflows.expr import template_errors, validate_expr

FORMAT = "flowpilot.workflow/v1"

NODE_TYPES: dict[str, dict] = {
    "start":        {"label": "Start / Trigger", "outputs": ["out"], "category": "Flow"},
    "end":          {"label": "End / Output", "outputs": [], "category": "Flow"},
    "llm":          {"label": "LLM / Agent", "outputs": ["out", "error"], "category": "AI"},
    "tool":         {"label": "Tool Call", "outputs": ["out", "error"], "category": "Actions"},
    "http":         {"label": "HTTP Request", "outputs": ["out", "error"], "category": "Actions"},
    "transform":    {"label": "Transform", "outputs": ["out", "error"], "category": "Data"},
    "set_variable": {"label": "Set Variable", "outputs": ["out"], "category": "Data"},
    "condition":    {"label": "Condition / Branch", "outputs": ["true", "false"], "category": "Flow"},
    "loop":         {"label": "Loop", "outputs": ["body", "done"], "category": "Flow"},
    "approval":     {"label": "Human Approval", "outputs": ["approved", "rejected"], "category": "Safety"},
    "delay":        {"label": "Delay", "outputs": ["out"], "category": "Flow"},
    "retry":        {"label": "Retry / Recovery Policy", "outputs": ["out"], "category": "Safety"},
    "subworkflow":  {"label": "Sub-workflow / Map", "outputs": ["out", "error"], "category": "Flow"},
}
TRANSFORM_OPS = ["template", "expression", "pick", "json_parse", "regex_extract", "merge"]


def empty_definition() -> dict:
    return {
        "format": FORMAT,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 80, "y": 160}, "data": {"label": "Start", "config": {"trigger": "manual", "input_schema": {}}}},
            {"id": "end", "type": "end", "position": {"x": 560, "y": 160}, "data": {"label": "End", "config": {"output": "{{ input }}"}}},
        ],
        "edges": [{"id": "e_start_end", "source": "start", "sourceHandle": "out", "target": "end"}],
        "policy": {},
    }


def content_hash(defn: dict) -> str:
    """Hash of the semantically relevant definition (positions excluded so dragging a node is not a new version)."""
    core = {
        "nodes": [{"id": n["id"], "type": n["type"], "data": n.get("data", {})} for n in sorted(defn.get("nodes", []), key=lambda n: n["id"])],
        "edges": sorted(({"s": e["source"], "t": e["target"], "h": e.get("sourceHandle") or "out"} for e in defn.get("edges", [])), key=lambda e: json.dumps(e, sort_keys=True)),
        "policy": defn.get("policy", {}),
    }
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def issue(level: str, message: str, node_id: str | None = None, field: str | None = None, edge_id: str | None = None) -> dict:
    return {"level": level, "message": message, "node_id": node_id, "field": field, "edge_id": edge_id}


def _retry_issues(cfg: dict, nid: str) -> list[dict]:
    r = cfg.get("retry")
    out = []
    if r:
        ma = r.get("max_attempts", 1)
        if not isinstance(ma, int) or ma < 1 or ma > config.HARD_MAX_RETRIES:
            out.append(issue("error", f"retry.max_attempts must be 1–{config.HARD_MAX_RETRIES}", nid, "retry"))
    return out


def validate_definition(defn: Any, agents: dict | None = None) -> list[dict]:
    """Return a list of issues. Any issue with level 'error' blocks execution."""
    issues: list[dict] = []
    if not isinstance(defn, dict):
        return [issue("error", "Workflow definition must be an object")]
    nodes, edges = defn.get("nodes"), defn.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [issue("error", "Workflow must contain 'nodes' and 'edges' arrays")]
    if len(json.dumps(defn, default=str)) > config.MAX_WORKFLOW_BYTES:
        return [issue("error", "Workflow is too large")]
    if len(nodes) > config.MAX_NODES:
        return [issue("error", f"Workflow has more than {config.MAX_NODES} nodes")]
    by_id: dict[str, dict] = {}
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not n["id"] or len(n["id"]) > 64:
            issues.append(issue("error", "Every node needs a string id (≤64 chars)"))
            continue
        if n["id"] in by_id:
            issues.append(issue("error", f"Duplicate node id '{n['id']}'", n["id"]))
        if n.get("type") not in NODE_TYPES:
            issues.append(issue("error", f"Unknown node type '{n.get('type')}'", n["id"]))
            continue
        if not isinstance(n.get("data", {}), dict) or not isinstance(n.get("data", {}).get("config", {}), dict):
            issues.append(issue("error", "Node data.config must be an object", n["id"]))
            continue
        by_id[n["id"]] = n
    starts = [n for n in by_id.values() if n["type"] == "start"]
    ends = [n for n in by_id.values() if n["type"] == "end"]
    if len(starts) != 1:
        issues.append(issue("error", f"Exactly one Start node is required (found {len(starts)})"))
    if not ends:
        issues.append(issue("error", "At least one End node is required"))

    out_edges: dict[tuple[str, str], list[dict]] = {}
    adj: dict[str, list[str]] = {i: [] for i in by_id}
    for e in edges:
        if not isinstance(e, dict) or e.get("source") not in by_id or e.get("target") not in by_id:
            issues.append(issue("error", "Edge references a node that does not exist", edge_id=(e or {}).get("id") if isinstance(e, dict) else None))
            continue
        src = by_id[e["source"]]
        handle = e.get("sourceHandle") or "out"
        if handle not in NODE_TYPES[src["type"]]["outputs"]:
            issues.append(issue("error", f"'{src['type']}' node has no '{handle}' output", src["id"], edge_id=e.get("id")))
            continue
        if e["target"] == starts[0]["id"] if starts else False:
            issues.append(issue("error", "Nothing may connect into the Start node", e["target"], edge_id=e.get("id")))
        out_edges.setdefault((e["source"], handle), []).append(e)
        adj[e["source"]].append(e["target"])
    for (src, handle), es in out_edges.items():
        if len(es) > 1:
            issues.append(issue("error", f"Output '{handle}' has {len(es)} connections; parallel branches are not supported", src))

    # reachability from start
    if starts:
        seen, stack = set(), [starts[0]["id"]]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack += adj.get(cur, [])
        for nid in by_id:
            if nid not in seen:
                issues.append(issue("warning", "Node is not reachable from Start", nid))
        if ends and not any(e["id"] in seen for e in ends):
            issues.append(issue("error", "No End node is reachable from Start"))
        # every cycle must pass through a loop node
        non_loop_adj = {k: [t for t in v if by_id[t]["type"] != "loop"] for k, v in adj.items()}
        color: dict[str, int] = {}

        def dfs(u: str) -> bool:
            color[u] = 1
            for v in non_loop_adj.get(u, []):
                if color.get(v) == 1 or (color.get(v) is None and dfs(v)):
                    return True
            color[u] = 2
            return False
        for nid in by_id:
            if color.get(nid) is None and dfs(nid):
                issues.append(issue("error", "Cycle detected that does not pass through a Loop node (unbounded loop)", nid))
                break

    for nid, n in by_id.items():
        cfg = n.get("data", {}).get("config", {})
        t = n["type"]
        issues += _retry_issues(cfg, nid)
        for err in template_errors(cfg):
            issues.append(issue("error", f"Invalid template {err}", nid))
        has = lambda k: cfg.get(k) not in (None, "")  # noqa: E731
        if t == "start":
            if cfg.get("trigger", "manual") not in ("manual", "webhook", "schedule"):
                issues.append(issue("error", "trigger must be 'manual', 'webhook' or 'schedule'", nid, "trigger"))
            if cfg.get("trigger") == "schedule":
                from backend.workflows import cron
                try:
                    cron.parse(cfg.get("cron", ""))
                except cron.CronError as e:
                    issues.append(issue("error", f"Invalid cron expression: {e}", nid, "cron"))
        elif t == "llm":
            if not has("prompt"):
                issues.append(issue("error", "Prompt is required", nid, "prompt"))
            if not has("provider") and not has("agent_id") and not (cfg.get("agent_snapshot") or {}).get("provider"):
                issues.append(issue("error", "Choose a provider/model or an agent", nid, "provider"))
            mi = cfg.get("max_iterations", 4)
            if not isinstance(mi, int) or not 1 <= mi <= config.HARD_MAX_AGENT_ITERATIONS:
                issues.append(issue("error", f"max_iterations must be 1–{config.HARD_MAX_AGENT_ITERATIONS}", nid, "max_iterations"))
            if agents is not None and cfg.get("agent_id") and cfg["agent_id"] not in agents and not cfg.get("agent_snapshot"):
                issues.append(issue("error", "Selected agent no longer exists", nid, "agent_id"))
        elif t == "tool":
            if not has("tool"):
                issues.append(issue("error", "Select a tool", nid, "tool"))
        elif t == "http":
            if not has("url"):
                issues.append(issue("error", "URL is required", nid, "url"))
            if str(cfg.get("method", "GET")).upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                issues.append(issue("error", "Unsupported HTTP method", nid, "method"))
        elif t == "transform":
            if cfg.get("op") not in TRANSFORM_OPS:
                issues.append(issue("error", f"Choose an operation: {', '.join(TRANSFORM_OPS)}", nid, "op"))
            elif cfg["op"] == "expression" and not has("expression"):
                issues.append(issue("error", "Expression is required", nid, "expression"))
            elif cfg["op"] == "template" and not has("template"):
                issues.append(issue("error", "Template is required", nid, "template"))
        elif t == "set_variable":
            if not has("name"):
                issues.append(issue("error", "Variable name is required", nid, "name"))
        elif t == "condition":
            if not has("expression"):
                issues.append(issue("error", "Condition expression is required", nid, "expression"))
            else:
                e = validate_expr(str(cfg["expression"]))
                if e:
                    issues.append(issue("error", f"Invalid expression: {e}", nid, "expression"))
            for h in ("true", "false"):
                if (nid, h) not in out_edges:
                    issues.append(issue("warning", f"'{h}' branch is not connected; the run will end there", nid))
        elif t == "loop":
            mi = cfg.get("max_iterations")
            if not isinstance(mi, int) or not 1 <= mi <= config.HARD_MAX_LOOP_ITERATIONS:
                issues.append(issue("error", f"max_iterations is required (1–{config.HARD_MAX_LOOP_ITERATIONS})", nid, "max_iterations"))
            if cfg.get("mode", "count") == "foreach" and not has("items"):
                issues.append(issue("error", "'items' expression is required for foreach", nid, "items"))
            if (nid, "body") not in out_edges:
                issues.append(issue("error", "Connect the 'body' output", nid))
        elif t == "approval":
            if (nid, "approved") not in out_edges:
                issues.append(issue("error", "Connect the 'approved' output", nid))
        elif t == "delay":
            s = cfg.get("seconds", 1)
            if not isinstance(s, (int, float)) or s < 0 or s > config.HARD_MAX_DELAY_SECONDS:
                issues.append(issue("error", f"seconds must be 0–{config.HARD_MAX_DELAY_SECONDS}", nid, "seconds"))
        elif t == "subworkflow":
            if not has("workflow_id"):
                issues.append(issue("error", "Choose the workflow to call", nid, "workflow_id"))
            if cfg.get("mode", "single") == "foreach":
                if not has("items"):
                    issues.append(issue("error", "'items' expression is required for map mode", nid, "items"))
                mi = cfg.get("max_items", 10)
                if not isinstance(mi, int) or not 1 <= mi <= 20:
                    issues.append(issue("error", "max_items must be 1–20", nid, "max_items"))
        elif t == "retry":
            if not isinstance(cfg.get("max_attempts", 3), int) or not 1 <= cfg.get("max_attempts", 3) <= config.HARD_MAX_RETRIES:
                issues.append(issue("error", f"max_attempts must be 1–{config.HARD_MAX_RETRIES}", nid, "max_attempts"))
            if (nid, "out") not in out_edges:
                issues.append(issue("error", "Connect the policy to the node it protects", nid))
        # non-terminal nodes must lead somewhere
        if t not in ("end",) and not any(k[0] == nid for k in out_edges) and t != "retry":
            issues.append(issue("warning", "Node has no outgoing connection; the run ends here", nid))
    issues += safety_lint(by_id, edges)
    return issues


def safety_lint(by_id: dict, edges: list) -> list[dict]:
    """Duplicate-side-effect risk analysis (warnings only)."""
    from backend.tools.builtin import REGISTRY  # lazy: avoids an import cycle with the db layer
    out: list[dict] = []
    upstream_retry: dict[str, dict] = {}
    for e in edges:
        if isinstance(e, dict) and e.get("source") in by_id and e.get("target") in by_id and by_id[e["source"]]["type"] == "retry":
            upstream_retry[e["target"]] = by_id[e["source"]]["data"].get("config", {})
    webhook = any(n["type"] == "start" and n["data"].get("config", {}).get("trigger") == "webhook" for n in by_id.values())
    dedupe = any(n["type"] == "start" and n["data"].get("config", {}).get("dedupe_key") for n in by_id.values())
    has_side_effect = False
    for nid, n in by_id.items():
        c = n.get("data", {}).get("config", {})
        side, tool = False, None
        if n["type"] == "tool" and c.get("tool") in REGISTRY:
            tool = REGISTRY[c["tool"]]
            side = tool.side_effect
        elif n["type"] == "http":
            side = str(c.get("method", "GET")).upper() not in ("GET", "HEAD")
        elif n["type"] == "llm":
            side = any(REGISTRY[t].side_effect for t in (c.get("tools") or []) if t in REGISTRY)
        if not side:
            continue
        has_side_effect = True
        pol = c.get("retry") or upstream_retry.get(nid) or {}
        if pol.get("safe_to_retry") and not c.get("idempotency_key"):
            out.append(issue("warning", "Duplicate risk: 'safe_to_retry' is on for a side effect with no idempotency key. A retry after an unknown outcome can repeat the action.", nid, "retry"))
        if n["type"] in ("tool", "http") and int(pol.get("max_attempts", 1) or 1) > 1 and not c.get("idempotency_key") and not pol.get("safe_to_retry"):
            out.append(issue("warning", "Retries are enabled on a side effect without an idempotency key. Known-not-executed failures will retry; an unknown outcome will pause for reconciliation.", nid, "idempotency_key"))
    if webhook and has_side_effect and not dedupe:
        out.append(issue("warning", "Webhook-triggered workflow performs side effects but has no event dedupe. Providers re-deliver webhooks; set 'dedupe key' on the Start node (e.g. {{ input.event_id }}) or send an Idempotency-Key header."))
    return out


def has_errors(issues: list[dict]) -> bool:
    return any(i["level"] == "error" for i in issues)
