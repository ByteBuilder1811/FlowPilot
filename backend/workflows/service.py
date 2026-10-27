import copy

from backend.db.models import Agent, Workflow, WorkflowVersion
from backend.db.session import new_id, now, session
from backend.workflows.schema import content_hash, empty_definition, has_errors, validate_definition


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, issues: list | None = None, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.issues, self.status = code, message, issues or [], status


def agents_map() -> dict:
    with session() as s:
        return {a.id: {"id": a.id, "name": a.name, "config": a.config} for a in s.query(Agent).all()}


def create_workflow(name: str, definition: dict | None = None, description: str = "", is_sample: bool = False) -> str:
    wid = new_id("wf")
    defn = definition or empty_definition()
    with session() as s:
        s.add(Workflow(id=wid, name=name[:200], description=description, draft=defn, is_sample=is_sample, created_at=now(), updated_at=now()))
    return wid


def save_draft(wid: str, definition: dict | None = None, name: str | None = None, description: str | None = None) -> dict:
    with session() as s:
        wf = s.get(Workflow, wid)
        if not wf or wf.deleted:
            raise WorkflowError("not_found", "Workflow not found", status=404)
        if definition is not None:
            if not isinstance(definition, dict) or not isinstance(definition.get("nodes"), list) or not isinstance(definition.get("edges"), list):
                raise WorkflowError("invalid_definition", "Definition must contain nodes and edges arrays")
            wf.draft = definition  # drafts may be invalid; they are validated on run/version
        if name is not None:
            wf.name = name.strip()[:200] or wf.name
        if description is not None:
            wf.description = description
        wf.updated_at = now()
        return {"id": wf.id, "name": wf.name, "updated_at": wf.updated_at.isoformat()}


def _embed_agents(defn: dict) -> dict:
    """Freeze referenced agent configs into the version so later agent edits never change history."""
    defn = copy.deepcopy(defn)
    amap = agents_map()
    for n in defn["nodes"]:
        cfg = n.get("data", {}).get("config", {})
        if n["type"] == "llm" and cfg.get("agent_id") in amap:
            a = amap[cfg["agent_id"]]
            cfg["agent_snapshot"] = {**a["config"], "agent_name": a["name"]}
    return defn


def snapshot_version(wid: str, note: str = "", definition: dict | None = None) -> dict:
    """Create an immutable version from the draft (or reuse the latest if nothing semantically changed)."""
    with session() as s:
        wf = s.get(Workflow, wid)
        if not wf or wf.deleted:
            raise WorkflowError("not_found", "Workflow not found", status=404)
        defn = definition if definition is not None else wf.draft
    issues = validate_definition(defn, agents_map())
    if has_errors(issues):
        raise WorkflowError("invalid_workflow", "Workflow has validation errors", issues)
    defn = _embed_agents(defn)
    h = content_hash(defn)
    with session() as s:
        last = s.query(WorkflowVersion).filter_by(workflow_id=wid).order_by(WorkflowVersion.version_no.desc()).first()
        if last and last.content_hash == h:
            return {"id": last.id, "version_no": last.version_no, "reused": True}
        v = WorkflowVersion(id=new_id("ver"), workflow_id=wid, version_no=(last.version_no + 1) if last else 1, definition=defn,
                            content_hash=h, note=note, created_at=now())
        s.add(v)
        s.flush()
        return {"id": v.id, "version_no": v.version_no, "reused": False}


def version_dict(v: WorkflowVersion, with_def: bool = False) -> dict:
    d = {"id": v.id, "workflow_id": v.workflow_id, "version_no": v.version_no, "note": v.note, "content_hash": v.content_hash[:12], "created_at": v.created_at.isoformat()}
    if with_def:
        d["definition"] = v.definition
    return d


def diff_definitions(a: dict, b: dict) -> dict:
    an, bn = {n["id"]: n for n in a["nodes"]}, {n["id"]: n for n in b["nodes"]}
    changed = []
    for nid in sorted(set(an) & set(bn)):
        na, nb = an[nid], bn[nid]
        fields = []
        if na["type"] != nb["type"]:
            fields.append({"path": "type", "a": na["type"], "b": nb["type"]})
        if na["data"].get("label") != nb["data"].get("label"):
            fields.append({"path": "label", "a": na["data"].get("label"), "b": nb["data"].get("label")})
        ca, cb = na["data"].get("config", {}), nb["data"].get("config", {})
        for k in sorted(set(ca) | set(cb)):
            if k == "agent_snapshot":
                continue
            if ca.get(k) != cb.get(k):
                fields.append({"path": f"config.{k}", "a": ca.get(k), "b": cb.get(k)})
        if fields:
            changed.append({"node_id": nid, "label": nb["data"].get("label") or nid, "type": nb["type"], "fields": fields})
    ek = lambda e: (e["source"], e.get("sourceHandle") or "out", e["target"])  # noqa: E731
    ea, eb = {ek(e) for e in a["edges"]}, {ek(e) for e in b["edges"]}
    fmt = lambda t: {"source": t[0], "handle": t[1], "target": t[2]}  # noqa: E731
    return {
        "nodes_added": [{"id": i, "type": bn[i]["type"], "label": bn[i]["data"].get("label")} for i in sorted(set(bn) - set(an))],
        "nodes_removed": [{"id": i, "type": an[i]["type"], "label": an[i]["data"].get("label")} for i in sorted(set(an) - set(bn))],
        "nodes_changed": changed,
        "edges_added": [fmt(t) for t in sorted(eb - ea)],
        "edges_removed": [fmt(t) for t in sorted(ea - eb)],
        "policy_changed": (a.get("policy") or {}) != (b.get("policy") or {}),
    }
