import asyncio
import hashlib
import os
import json
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func

from backend import config
from sqlalchemy.exc import IntegrityError

from backend.db.models import (Agent, ApprovalRequest, IdempotencyKey, MockOutbox, ProviderConfiguration, RegressionCase, RegressionResult, Run, RunStep,
                               SecretReference, ToolCall, Workflow, WorkflowVersion)
from backend.db.session import new_id, now, session
from backend.execution import store
from backend.execution.settings_store import get_settings, put_settings
from backend.execution.state import ACTIVE, TERMINAL, IllegalTransition
from backend.providers.registry import all_statuses
from backend.replay import evals as evals_mod, insights as insights_mod, regression
from backend.security import auth, oauth as oauth_mod, vault
from backend.security.redaction import redact
from backend.tools.builtin import REGISTRY
from backend.workflows import service
from backend.tools import knowledge as kb_service
from backend.db.models import AgentMemory, AuditEntry, Credential, OAuthApp, EvalDataset, EvalResult, EvalRow, EvalRun, KnowledgeBase, KnowledgeChunk, KnowledgeDoc
from backend.workflows import cron as cron_mod
from backend.workflows.examples import EXAMPLES
from backend.workflows.schema import FORMAT, NODE_TYPES, TRANSFORM_OPS, has_errors, validate_definition

from backend.security import users  # noqa: E402

_READ_EXEMPT = ("/api/health", "/api/session", "/api/auth/", "/api/oauth/callback")


def read_guard(request: Request):
    """In team mode every read needs a signed-in viewer (SSE streams use the same cookie)."""
    if request.method in ("GET", "HEAD") and users.get_mode() == "users" and not request.url.path.startswith(_READ_EXEMPT):
        request.state.actor = users.authorize(request, "viewer", write=False)


def editor(request: Request):
    request.state.actor = users.authorize(request, "editor", write=True)


def admin_role(request: Request):
    request.state.actor = users.authorize(request, "admin", write=True)


router = APIRouter(prefix="/api", dependencies=[Depends(read_guard)])
admin = [Depends(editor)]
admin_only = [Depends(admin_role)]


def err(status: int, code: str, message: str, **extra):
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def wf_guard(e: service.WorkflowError):
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message, "issues": e.issues})


def iso(d: datetime | None):
    return d.isoformat() if d else None


# ------------------------------------------------------------------ session / meta
@router.get("/health")
def health():
    return {"ok": True, "version": "0.1.0", "mode": "local-single-user"}


@router.get("/session")
def get_session(request: Request):
    if not auth.host_is_local(request) or not auth.origin_is_local(request):
        raise err(403, "forbidden", "The admin token is only issued to local requests")
    if users.get_mode() == "users":  # team mode: no shared token; browsers sign in
        return {"token": None, "mode": "users"}
    return {"token": auth.admin_token(), "mode": "local-single-user"}


@router.get("/meta")
def meta():
    return {"node_types": NODE_TYPES, "transform_ops": TRANSFORM_OPS, "format": FORMAT, "fault_kinds": regression.FAULT_KINDS,
            "tools": [t.public() for t in REGISTRY.values()], "providers": all_statuses(),
            "examples": [{"key": k, "name": v["name"], "description": v["description"], "category": v["category"]} for k, v in EXAMPLES.items()],
            "limits": {"max_steps": config.HARD_MAX_STEPS, "max_loop_iterations": config.HARD_MAX_LOOP_ITERATIONS, "max_agent_iterations": config.HARD_MAX_AGENT_ITERATIONS,
                       "max_retries": config.HARD_MAX_RETRIES}}


# ------------------------------------------------------------------ workflows
class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: dict | None = None


class WorkflowPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    definition: dict | None = None


def wf_summary(w: Workflow, s) -> dict:
    last = s.query(WorkflowVersion).filter_by(workflow_id=w.id).order_by(WorkflowVersion.version_no.desc()).first()
    runs = s.query(Run.status, func.count()).filter(Run.workflow_id == w.id, Run.mode == "live").group_by(Run.status).all()
    return {"id": w.id, "name": w.name, "description": w.description, "is_sample": w.is_sample, "updated_at": iso(w.updated_at), "created_at": iso(w.created_at),
            "node_count": len(w.draft.get("nodes", [])), "latest_version": last.version_no if last else None, "run_counts": {k: v for k, v in runs},
            "trigger": next((n["data"]["config"].get("trigger", "manual") for n in w.draft.get("nodes", []) if n["type"] == "start"), "manual")}


def schedule_info(w: Workflow) -> dict:
    cfg = next((n["data"].get("config", {}) for n in w.draft.get("nodes", []) if n.get("type") == "start"), {})
    if cfg.get("trigger") != "schedule":
        return {"configured": False, "enabled": False}
    try:
        nxt = cron_mod.next_fire(cfg.get("cron", ""), now())
    except cron_mod.CronError:
        nxt = None
    return {"configured": True, "enabled": bool(w.schedule_enabled), "cron": cfg.get("cron"), "next_fire": nxt.isoformat() if nxt else None, "timezone": "UTC"}


@router.get("/workflows")
def list_workflows():
    with session() as s:
        return [wf_summary(w, s) for w in s.query(Workflow).filter_by(deleted=False).order_by(Workflow.updated_at.desc()).all()]


@router.post("/workflows", dependencies=admin)
def create_workflow(body: WorkflowIn):
    wid = service.create_workflow(body.name, body.definition, body.description)
    return {"id": wid}


@router.get("/workflows/{wid}")
def get_workflow(wid: str):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        vs = s.query(WorkflowVersion).filter_by(workflow_id=wid).order_by(WorkflowVersion.version_no.desc()).all()
        return {**wf_summary(w, s), "draft": w.draft, "versions": [service.version_dict(v) for v in vs], "webhook_enabled": bool(w.webhook_token_hash), "publish_enabled": bool(w.publish_token_hash), "schedule": schedule_info(w),
                "issues": validate_definition(w.draft, service.agents_map())}


@router.put("/workflows/{wid}", dependencies=admin)
def save_workflow(wid: str, body: WorkflowPatch):
    try:
        out = service.save_draft(wid, body.definition, body.name, body.description)
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    with session() as s:
        w = s.get(Workflow, wid)
        out["issues"] = validate_definition(w.draft, service.agents_map())
    return out


@router.post("/workflows/{wid}/validate")
def validate_workflow(wid: str, body: dict | None = None):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        d = (body or {}).get("definition") or w.draft
    issues = validate_definition(d, service.agents_map())
    return {"issues": issues, "valid": not has_errors(issues)}


@router.post("/workflows/{wid}/duplicate", dependencies=admin)
def duplicate_workflow(wid: str):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        name, d, desc = w.name, w.draft, w.description
    return {"id": service.create_workflow(f"{name} (copy)", json.loads(json.dumps(d)), desc)}


@router.delete("/workflows/{wid}", dependencies=admin)
def delete_workflow(wid: str):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        w.deleted = True  # soft delete: run history and versions stay intact
    return {"ok": True}


class VersionIn(BaseModel):
    note: str = ""


@router.post("/workflows/{wid}/versions", dependencies=admin)
def create_version(wid: str, body: VersionIn):
    try:
        return service.snapshot_version(wid, body.note)
    except service.WorkflowError as e:
        raise wf_guard(e) from e


@router.get("/workflows/{wid}/versions/{vid}")
def get_version(wid: str, vid: str):
    with session() as s:
        v = s.get(WorkflowVersion, vid)
        if not v or v.workflow_id != wid:
            raise err(404, "not_found", "Version not found")
        return service.version_dict(v, True)


@router.get("/workflows/{wid}/diff")
def diff_versions(wid: str, a: str, b: str):
    with session() as s:
        A, B = s.get(WorkflowVersion, a), s.get(WorkflowVersion, b)
        if not A or not B or A.workflow_id != wid or B.workflow_id != wid:
            raise err(404, "not_found", "Version not found")
        return {"a": service.version_dict(A), "b": service.version_dict(B), "diff": service.diff_definitions(A.definition, B.definition)}


@router.get("/workflows/{wid}/export")
def export_workflow(wid: str):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        d = json.loads(json.dumps(w.draft))
    for n in d["nodes"]:
        n["data"].get("config", {}).pop("agent_snapshot", None)
    return redact({"format": FORMAT, "name": w.name, "description": w.description, "definition": d, "exported_at": now().isoformat()})


@router.post("/workflows/import", dependencies=admin)
def import_workflow(body: dict):
    if body.get("format") != FORMAT:
        raise err(400, "bad_format", f"Expected format '{FORMAT}'")
    d = body.get("definition")
    issues = validate_definition(d, None)
    structural = [i for i in issues if i["level"] == "error" and i["node_id"] is None and i["edge_id"] is None]
    if not isinstance(d, dict) or structural:
        raise err(400, "invalid_workflow", "Imported workflow is not valid", issues=issues)
    return {"id": service.create_workflow(str(body.get("name", "Imported workflow"))[:200], d, str(body.get("description", ""))), "issues": issues}


@router.post("/examples/{key}/load", dependencies=admin)
def load_example(key: str):
    ex = EXAMPLES.get(key)
    if not ex:
        raise err(404, "not_found", "Unknown example")
    if ex.get("setup") == "support_kb":
        with session() as s:
            kb = s.query(KnowledgeBase).filter_by(name="sample-handbook").first()
        if not kb:
            kid = new_id("kb")
            with session() as s:
                s.add(KnowledgeBase(id=kid, name="sample-handbook", description="SAMPLE data created by the support template", created_at=now()))
            kb_service.add_document(kid, "Refund policy", "Refunds are issued within 14 days of purchase. Contact support with your order number.\n\nRefunds go back to the original payment method.", "sample")
            kb_service.add_document(kid, "Shipping", "Standard shipping takes 3-5 business days. Express shipping takes 1-2 business days.", "sample")
    wid = service.create_workflow(ex["name"], json.loads(json.dumps(ex["definition"])), ex["description"], is_sample=True)
    v = service.snapshot_version(wid, "Initial sample version")
    return {"id": wid, "version_id": v["id"], "sample_input": ex["sample_input"]}


@router.get("/examples/{key}/input")
def example_input(key: str):
    if key not in EXAMPLES:
        raise err(404, "not_found", "Unknown example")
    return EXAMPLES[key]["sample_input"]


# ------------------------------------------------------------------ webhook
@router.post("/workflows/{wid}/webhook-token", dependencies=admin)
def rotate_webhook_token(wid: str):
    tok = "whk_" + secrets.token_urlsafe(24)
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        w.webhook_token_hash = hashlib.sha256(tok.encode()).hexdigest()
    return {"token": tok, "note": "Shown once. Send it as the X-FlowPilot-Webhook-Token header.", "url": f"/api/hooks/{wid}"}


@router.post("/hooks/{wid}")
async def webhook(wid: str, request: Request):
    raw = await request.body()
    if len(raw) > config.MAX_PAYLOAD_BYTES:
        raise err(413, "payload_too_large", "Payload exceeds the size limit")
    tok = request.headers.get("x-flowpilot-webhook-token", "")
    with session() as s:
        w = s.get(Workflow, wid)
        ok = bool(w and not w.deleted and w.webhook_token_hash and secrets.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), w.webhook_token_hash))
        draft = w.draft if ok else None
    if not ok:
        raise err(401, "unauthorized", "Invalid webhook token")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise err(400, "invalid_json", "Body must be JSON") from None
    if not isinstance(payload, dict):
        raise err(400, "invalid_json", "Body must be a JSON object")
    start = next((n for n in draft["nodes"] if n["type"] == "start"), None)
    if not start or start["data"]["config"].get("trigger") != "webhook":
        raise err(409, "not_webhook", "This workflow's Start node is not configured as a webhook trigger")
    try:
        v = service.snapshot_version(wid, "Snapshot for webhook run")
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    key = request.headers.get("idempotency-key") or request.headers.get("x-flowpilot-event-id")
    if not key and start["data"]["config"].get("dedupe_key"):
        from backend.workflows.expr import ExprError, render
        try:
            k = render(start["data"]["config"]["dedupe_key"], {"input": payload})
            key = str(k) if k not in (None, "") else None
        except ExprError:
            raise err(422, "bad_dedupe_key", "The Start node's dedupe key expression could not be evaluated for this payload") from None
    if key:
        key = key[:200]
        with session() as s:
            prev = s.query(IdempotencyKey).filter_by(workflow_id=wid, key=key).first()
            prev_run = s.get(Run, prev.run_id) if prev else None
            prev_status = prev_run.status if prev_run else None
        # 'seen' is not 'done': in-flight or succeeded deliveries are deduped, failed/cancelled ones may be re-attempted
        if prev and prev_status in ACTIVE | {"succeeded"}:
            store.emit(prev.run_id, "webhook.duplicate_ignored", {"key": key})
            return {"run_id": prev.run_id, "status": prev_status, "duplicate": True}
        rid = store.create_run(wid, v["id"], payload, trigger="webhook", source_run_id=prev.run_id if prev else None)
        try:
            with session() as s:
                if prev:
                    s.query(IdempotencyKey).filter_by(workflow_id=wid, key=key).update({"run_id": rid})
                else:
                    s.add(IdempotencyKey(workflow_id=wid, key=key, run_id=rid, created_at=now()))
        except IntegrityError:  # concurrent delivery won the race
            with session() as s:
                winner = s.query(IdempotencyKey).filter_by(workflow_id=wid, key=key).first().run_id
            store.transition(rid, "cancelled", error={"code": "duplicate_delivery", "message": "Concurrent duplicate delivery"}, quiet=True)
            return {"run_id": winner, "status": run_status(winner), "duplicate": True}
        return {"run_id": rid, "status": "queued", "duplicate": False, "retry_of": prev.run_id if prev else None}
    rid = store.create_run(wid, v["id"], payload, trigger="webhook")
    return {"run_id": rid, "status": "queued", "duplicate": False}


class ScheduleIn(BaseModel):
    enabled: bool


@router.post("/workflows/{wid}/schedule", dependencies=admin)
def set_schedule(wid: str, body: ScheduleIn):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        if body.enabled:
            info = schedule_info(w)
            if not info["configured"]:
                raise err(409, "not_scheduled", "Set the Start node's trigger to 'schedule' and save first")
            if not info["next_fire"]:
                raise err(422, "bad_cron", "The cron expression is invalid")
            issues = validate_definition(w.draft, service.agents_map())
            if has_errors(issues):
                raise err(422, "invalid_workflow", "Fix validation errors before enabling the schedule", issues=issues)
        w.schedule_enabled = body.enabled
        w.last_fired_minute = None
        return schedule_info(w)


@router.post("/workflows/{wid}/publish-token", dependencies=admin)
def rotate_publish_token(wid: str):
    tok = "fpk_" + secrets.token_urlsafe(28)
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        w.publish_token_hash = hashlib.sha256(tok.encode()).hexdigest()
    return {"token": tok, "note": "Shown once. Send as X-FlowPilot-Api-Key.", "url": f"/api/v1/workflows/{wid}/invoke"}


@router.delete("/workflows/{wid}/publish-token", dependencies=admin)
def revoke_publish_token(wid: str):
    with session() as s:
        w = s.get(Workflow, wid)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        w.publish_token_hash = None
    return {"ok": True}


class InvokeIn(BaseModel):
    input: dict = Field(default_factory=dict)
    wait_seconds: float = Field(default=20, ge=0, le=60)


@router.post("/v1/workflows/{wid}/invoke")
def invoke_published(wid: str, body: InvokeIn, request: Request):
    """Published workflow as an authenticated API: returns the result synchronously when it finishes within wait_seconds."""
    tok = request.headers.get("x-flowpilot-api-key", "")
    with session() as s:
        w = s.get(Workflow, wid)
        ok = bool(w and not w.deleted and w.publish_token_hash and secrets.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), w.publish_token_hash))
    if not ok:
        raise err(401, "unauthorized", "Invalid or missing X-FlowPilot-Api-Key")
    if len(json.dumps(body.input)) > config.MAX_PAYLOAD_BYTES:
        raise err(413, "payload_too_large", "Input too large")
    try:
        v = service.snapshot_version(wid, "Snapshot for API invocation")
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    rid = store.create_run(wid, v["id"], body.input, trigger="api")
    from backend.execution.worker import wait_for_run
    try:
        wait_for_run(rid, body.wait_seconds, until=TERMINAL | {"waiting_for_approval"})
    except TimeoutError:
        pass
    with session() as s:
        r = s.get(Run, rid)
        return {"run_id": rid, "status": r.status, "output": r.output if r.status == "succeeded" else None, "error": r.error}


# ------------------------------------------------------------------ knowledge bases
class KbIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9 _.\-]+$")
    description: str = ""
    embedding: str = Field(default="lexical", pattern="^(lexical|ngram|remote)$")


class DocIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)
    source: str = ""


@router.get("/knowledge")
def list_kbs():
    with session() as s:
        out = []
        for k in s.query(KnowledgeBase).order_by(KnowledgeBase.name).all():
            out.append({"id": k.id, "name": k.name, "description": k.description, "embedding": k.embedding, "documents": s.query(KnowledgeDoc).filter_by(kb_id=k.id).count(),
                        "chunks": s.query(KnowledgeChunk).filter_by(kb_id=k.id).count()})
        return out


@router.post("/knowledge", dependencies=admin)
def create_kb(body: KbIn):
    with session() as s:
        if s.query(KnowledgeBase).filter_by(name=body.name).first():
            raise err(409, "exists", "A knowledge base with this name already exists")
        kid = new_id("kb")
        s.add(KnowledgeBase(id=kid, name=body.name, description=body.description, embedding=body.embedding, created_at=now()))
    return {"id": kid}


@router.get("/knowledge/{kid}")
def get_kb(kid: str):
    with session() as s:
        k = s.get(KnowledgeBase, kid)
        if not k:
            raise err(404, "not_found", "Knowledge base not found")
        docs = s.query(KnowledgeDoc).filter_by(kb_id=kid).order_by(KnowledgeDoc.created_at.desc()).all()
        return {"id": k.id, "name": k.name, "description": k.description, "embedding": k.embedding,
                "documents": [{"id": d.id, "title": d.title, "source": d.source, "chunks": s.query(KnowledgeChunk).filter_by(doc_id=d.id).count(), "created_at": iso(d.created_at)} for d in docs]}


@router.delete("/knowledge/{kid}", dependencies=admin)
def delete_kb(kid: str):
    with session() as s:
        if not s.get(KnowledgeBase, kid):
            raise err(404, "not_found", "Knowledge base not found")
        s.query(KnowledgeChunk).filter_by(kb_id=kid).delete()
        s.query(KnowledgeDoc).filter_by(kb_id=kid).delete()
        s.query(KnowledgeBase).filter_by(id=kid).delete()
    return {"ok": True}


@router.post("/knowledge/{kid}/documents", dependencies=admin)
def add_doc(kid: str, body: DocIn):
    try:
        return kb_service.add_document(kid, body.title, body.text, body.source)
    except KeyError:
        raise err(404, "not_found", "Knowledge base not found") from None
    except ValueError as e:
        raise err(422, "invalid_document", str(e)) from None
    except Exception as e:  # noqa: BLE001 — remote embedding misconfiguration etc.
        from backend.tools.base import ToolError
        if isinstance(e, ToolError):
            raise err(422, "embedding_unavailable", e.message) from None
        raise


@router.post("/knowledge/{kid}/reindex", dependencies=admin)
def reindex_kb(kid: str):
    from backend.tools.base import ToolError
    try:
        return kb_service.reindex(kid)
    except KeyError:
        raise err(404, "not_found", "Knowledge base not found") from None
    except ToolError as e:
        raise err(422, "embedding_unavailable", e.message) from None


@router.delete("/knowledge/{kid}/documents/{did}", dependencies=admin)
def delete_doc(kid: str, did: str):
    with session() as s:
        d = s.get(KnowledgeDoc, did)
        if not d or d.kb_id != kid:
            raise err(404, "not_found", "Document not found")
        s.query(KnowledgeChunk).filter_by(doc_id=did).delete()
        s.delete(d)
    return {"ok": True}


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)


@router.post("/knowledge/{kid}/search")
def search_kb(kid: str, body: SearchIn):
    from backend.tools.base import ToolError
    try:
        return {"results": kb_service.search(kid, body.query, 5)}
    except ToolError as e:
        raise err(404, "not_found", e.message) from None


# ------------------------------------------------------------------ runs
class RunIn(BaseModel):
    input: dict = Field(default_factory=dict)
    version_id: str | None = None


def run_dict(r: Run, brief: bool = True) -> dict:
    d = {"id": r.id, "workflow_id": r.workflow_id, "version_id": r.version_id, "status": r.status, "mode": r.mode, "trigger": r.trigger,
         "current_node": r.current_node, "cancel_requested": r.cancel_requested, "created_at": iso(r.created_at), "started_at": iso(r.started_at),
         "completed_at": iso(r.completed_at), "error": r.error, "usage": r.usage, "source_run_id": r.source_run_id, "case_id": r.case_id, "step_count": r.step_count}
    if not brief:
        d.update(input=r.input, output=r.output)
    return d


@router.post("/workflows/{wid}/run", dependencies=admin)
def start_run(wid: str, body: RunIn):
    try:
        if body.version_id:
            vid = body.version_id
        else:
            vid = service.snapshot_version(wid, "")["id"]
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    if len(json.dumps(body.input)) > config.MAX_PAYLOAD_BYTES:
        raise err(413, "payload_too_large", "Input too large")
    with session() as s:
        v = s.get(WorkflowVersion, vid)
        if not v or v.workflow_id != wid:
            raise err(404, "not_found", "Version not found")
    rid = store.create_run(wid, vid, body.input)
    return {"run_id": rid}


@router.get("/runs")
def list_runs(workflow_id: str | None = None, status: str | None = None, mode: str | None = None, limit: int = Query(50, le=200), offset: int = 0):
    with session() as s:
        q = s.query(Run)
        if workflow_id:
            q = q.filter_by(workflow_id=workflow_id)
        if status:
            q = q.filter_by(status=status)
        if mode:
            q = q.filter_by(mode=mode)
        total = q.count()
        rows = q.order_by(Run.created_at.desc()).offset(offset).limit(limit).all()
        names = {w.id: w.name for w in s.query(Workflow).all()}
        vers = {v.id: v.version_no for v in s.query(WorkflowVersion).all()}
        return {"total": total, "runs": [{**run_dict(r), "workflow_name": names.get(r.workflow_id), "version_no": vers.get(r.version_id)} for r in rows]}


@router.get("/runs/{rid}")
def get_run(rid: str):
    with session() as s:
        r = s.get(Run, rid)
        if not r:
            raise err(404, "not_found", "Run not found")
        wf = s.get(Workflow, r.workflow_id)
        ver = s.get(WorkflowVersion, r.version_id)
        steps = s.query(RunStep).filter_by(run_id=rid).order_by(RunStep.started_at).all()
        calls = s.query(ToolCall).filter_by(run_id=rid).order_by(ToolCall.started_at, ToolCall.seq).all()
        aps = s.query(ApprovalRequest).filter_by(run_id=rid).order_by(ApprovalRequest.created_at).all()
        return {**run_dict(r, False), "workflow_name": wf.name if wf else None, "version_no": ver.version_no, "definition": redact(ver.definition), "options_summary": {
                    "fixtures": len(r.options.get("fixtures", [])), "faults": r.options.get("faults", [])},
                "steps": [{"id": t.id, "node_id": t.node_id, "node_type": t.node_type, "visit": t.visit, "status": t.status, "input": t.input, "output": t.output, "handle": t.handle,
                           "error": t.error, "attempts": t.attempts, "started_at": iso(t.started_at), "ended_at": iso(t.ended_at)} for t in steps],
                "tool_calls": [{"id": c.id, "node_id": c.node_id, "kind": c.kind, "tool": c.tool, "attempt": c.attempt, "ordinal": c.ordinal, "args": c.args, "outcome": c.outcome,
                                "result": c.result, "error": c.error, "side_effect": c.side_effect, "idempotency_key": c.idempotency_key, "approval_id": c.approval_id,
                                "source": c.source, "started_at": iso(c.started_at), "ended_at": iso(c.ended_at)} for c in calls],
                "approvals": [approval_dict(a) for a in aps],
                "children": [{"id": k.id, "status": k.status, "key": k.parent_key, "workflow_id": k.workflow_id} for k in s.query(Run).filter(Run.parent_run_id == rid).order_by(Run.created_at).all()],
                "parent_run_id": r.parent_run_id}


@router.get("/runs/{rid}/effects")
def run_effects(rid: str):
    """Side-effect ledger: what this run actually did (or may have done) to external systems."""
    with session() as s:
        r = s.get(Run, rid)
        if not r:
            raise err(404, "not_found", "Run not found")
        rows = s.query(ToolCall).filter_by(run_id=rid, side_effect=True).order_by(ToolCall.started_at, ToolCall.seq).all()
        entries = []
        for c in rows:
            if c.source in ("fixture", "simulated"):
                state = "simulated"
            elif c.source == "reconciled":
                state = "reconciled"
            elif c.outcome == "ok":
                state = "executed"
            elif c.outcome in ("unknown", "started") or (c.error or {}).get("executed") is None:
                state = "unknown"
            else:
                state = "not_executed"
            tgt = (c.args or {}).get("to") or (c.args or {}).get("url") or (c.args or {}).get("customer")
            entries.append({"tool": c.tool, "node_id": c.node_id, "attempt": c.attempt, "state": state, "target": tgt, "approval_id": c.approval_id,
                            "idempotency_key": c.idempotency_key, "at": iso(c.started_at), "error": (c.error or {}).get("message")})
        counts = {k: sum(1 for e in entries if e["state"] == k) for k in ("executed", "unknown", "reconciled", "simulated", "not_executed")}
        return {"run_id": rid, "mode": r.mode, "entries": entries, "counts": counts,
                "unapproved": sum(1 for e in entries if e["state"] in ("executed", "unknown") and not e["approval_id"]),
                "needs_attention": counts["unknown"] > 0 and r.status not in ("succeeded",)}


def approval_dict(a: ApprovalRequest) -> dict:
    return {"id": a.id, "run_id": a.run_id, "node_id": a.node_id, "kind": a.kind, "status": a.status, "proposed": a.proposed, "decision": a.decision,
            "created_at": iso(a.created_at), "decided_at": iso(a.decided_at)}


@router.get("/runs/{rid}/events")
def run_events(rid: str, after: int = 0, limit: int = Query(500, le=2000)):
    with session() as s:
        if not s.get(Run, rid):
            raise err(404, "not_found", "Run not found")
    return {"events": store.events_after(rid, after, limit)}


@router.get("/runs/{rid}/events/stream")
async def stream_events(rid: str, request: Request, after: int = 0):
    """SSE. Each event carries `id: <seq>`; browsers resend Last-Event-ID on reconnect, `?after=` also works."""
    last = int(request.headers.get("last-event-id") or after or 0)
    with session() as s:
        if not s.get(Run, rid):
            raise err(404, "not_found", "Run not found")

    async def gen():
        nonlocal last
        idle = 0
        yield "retry: 1500\n\n"
        while True:
            if await request.is_disconnected():
                return
            evs = await asyncio.to_thread(store.events_after, rid, last)
            for e in evs:
                last = e["seq"]
                yield f"id: {e['seq']}\nevent: run_event\ndata: {json.dumps(e)}\n\n"
            with session() as s:
                st = s.get(Run, rid).status
            if st in TERMINAL and not evs:
                yield f"event: end\ndata: {json.dumps({'status': st})}\n\n"
                return
            idle = 0 if evs else idle + 1
            if idle % 50 == 49:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{rid}/cancel", dependencies=admin)
def cancel_run(rid: str):
    with session() as s:
        r = s.get(Run, rid)
        if not r:
            raise err(404, "not_found", "Run not found")
        st = r.status
        if st in TERMINAL:
            raise err(409, "already_finished", f"Run is already {st}")
        r.cancel_requested = True
    store.emit(rid, "run.cancel_requested", {})
    with session() as s:  # cascade to active child runs
        kids = [k.id for k in s.query(Run).filter(Run.parent_run_id == rid, Run.status.in_(list(ACTIVE))).all()]
    for k in kids:
        try:
            cancel_run(k)
        except HTTPException:
            pass
    if st in ("queued", "waiting_for_approval", "waiting_for_child"):  # no active worker to notice: finalize now
        try:
            store.transition(rid, "cancelled", error={"code": "cancelled", "message": "Cancelled on request"}, event_data={"while": st})
        except IllegalTransition:
            pass
    return {"ok": True, "status": run_status(rid)}


def run_status(rid: str) -> str:
    with session() as s:
        return s.get(Run, rid).status


@router.post("/runs/{rid}/rerun", dependencies=admin)
def rerun(rid: str):
    """LIVE rerun: calls real tools/providers again. May differ from the original and may cause external side effects."""
    with session() as s:
        r = s.get(Run, rid)
        if not r:
            raise err(404, "not_found", "Run not found")
        wid, vid, inp = r.workflow_id, r.version_id, r.input
    return {"run_id": store.create_run(wid, vid, inp, source_run_id=rid), "mode": "live"}


@router.post("/runs/{rid}/replay", dependencies=admin)
def replay(rid: str):
    """FIXTURE replay: reuses recorded tool/LLM results. Not a new real-world execution."""
    with session() as s:
        r = s.get(Run, rid)
        if not r:
            raise err(404, "not_found", "Run not found")
        if r.status not in TERMINAL:
            raise err(409, "run_not_finished", "Only finished runs can be replayed")
        wid, vid, inp = r.workflow_id, r.version_id, r.input
    fixtures, approvals = regression.build_fixtures(rid)
    return {"run_id": store.create_run(wid, vid, inp, mode="fixture_replay", trigger="replay", options={"fixtures": fixtures, "approvals": approvals}, source_run_id=rid), "mode": "fixture_replay"}


# ------------------------------------------------------------------ approvals
class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=1000)
    action: str | None = Field(default=None, pattern="^(retry|mark_done|fail)$")  # reconcile approvals only
    output: dict | None = None


@router.get("/approvals")
def list_approvals(status: str | None = "pending"):
    with session() as s:
        q = s.query(ApprovalRequest)
        if status:
            q = q.filter_by(status=status)
        rows = q.order_by(ApprovalRequest.created_at.desc()).limit(100).all()
        runs = {r.id: r for r in s.query(Run).filter(Run.id.in_([a.run_id for a in rows] or [""])).all()}
        names = {w.id: w.name for w in s.query(Workflow).all()}
        return [{**approval_dict(a), "workflow_name": names.get(runs[a.run_id].workflow_id), "run_status": runs[a.run_id].status} for a in rows if a.run_id in runs]


@router.post("/approvals/{aid}/decide", dependencies=admin)
def decide(aid: str, body: DecisionIn, request: Request):
    with session() as s:
        a = s.get(ApprovalRequest, aid)
        if not a:
            raise err(404, "not_found", "Approval not found")
        run = s.get(Run, a.run_id)
        if a.status != "pending":
            raise err(409, "already_decided", f"This request was already {a.status}")
        if run.status != "waiting_for_approval":
            raise err(409, "run_not_waiting", f"Run is {run.status}; the request is no longer actionable")
        if a.kind == "reconcile" and body.decision == "approve" and body.action is None:
            raise err(422, "action_required", "Choose retry, mark_done or fail for a reconciliation request")
        a.status = "approved" if body.decision == "approve" else "rejected"
        a.decision = redact({"note": body.note, "action": body.action, "output": body.output, "decided_by": getattr(request.state, "actor", "local-user")})
        a.decided_at = now()
        rid = run.id
    store.emit(rid, "approval.decided", {"approval_id": aid, "decision": body.decision, "note": body.note, "action": body.action}, a.node_id)
    store.transition(rid, "queued", event_data={"reason": "approval decided"}, quiet=True)
    return {"ok": True, "run_id": rid}


# ------------------------------------------------------------------ agents
class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    config: dict = Field(default_factory=dict)


def _agent_check(cfg: dict):
    if int(cfg.get("max_iterations", 4)) > config.HARD_MAX_AGENT_ITERATIONS or int(cfg.get("max_iterations", 4)) < 1:
        raise err(422, "invalid_agent", f"max_iterations must be 1–{config.HARD_MAX_AGENT_ITERATIONS}")
    for t in cfg.get("tools", []):
        if t not in REGISTRY:
            raise err(422, "invalid_agent", f"Unknown tool '{t}'")


@router.get("/agents")
def list_agents():
    with session() as s:
        return [{"id": a.id, "name": a.name, "description": a.description, "config": a.config, "updated_at": iso(a.updated_at)} for a in s.query(Agent).order_by(Agent.name).all()]


@router.post("/agents", dependencies=admin)
def create_agent(body: AgentIn):
    _agent_check(body.config)
    aid = new_id("agt")
    with session() as s:
        s.add(Agent(id=aid, name=body.name, description=body.description, config=body.config, created_at=now(), updated_at=now()))
    return {"id": aid}


@router.put("/agents/{aid}", dependencies=admin)
def update_agent(aid: str, body: AgentIn):
    _agent_check(body.config)
    with session() as s:
        a = s.get(Agent, aid)
        if not a:
            raise err(404, "not_found", "Agent not found")
        a.name, a.description, a.config, a.updated_at = body.name, body.description, body.config, now()
    return {"ok": True}


@router.delete("/agents/{aid}", dependencies=admin)
def delete_agent(aid: str):
    with session() as s:
        a = s.get(Agent, aid)
        if not a:
            raise err(404, "not_found", "Agent not found")
        s.delete(a)
    return {"ok": True}


# ------------------------------------------------------------------ providers / settings
class ProviderIn(BaseModel):
    enabled: bool = True
    default_model: str | None = None
    env_var: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.get("/providers")
def providers():
    return all_statuses()


@router.put("/providers/{name}", dependencies=admin_only)
def configure_provider(name: str, body: ProviderIn):
    if name not in ("mock", "anthropic"):
        raise err(404, "not_found", "Unknown provider")
    with session() as s:
        cfg = s.get(ProviderConfiguration, name) or ProviderConfiguration(provider=name)
        cfg.enabled, cfg.default_model, cfg.updated_at = body.enabled, body.default_model, now()
        if body.env_var:
            ref = s.query(SecretReference).filter_by(name=f"{name}_api_key").first()
            if ref:
                ref.env_var = body.env_var
            else:
                s.add(SecretReference(id=new_id("sec"), name=f"{name}_api_key", env_var=body.env_var))
            cfg.secret_ref = f"{name}_api_key"
        s.merge(cfg)
    return {"ok": True}


@router.post("/providers/{name}/test", dependencies=admin_only)
def test_provider(name: str):
    from backend.providers.base import ProviderError
    from backend.providers.registry import get_provider
    try:
        p = get_provider(name)
        st = p.status()
        if not st["configured"]:
            return {"ok": False, "message": st["message"]}
        r = p.complete(model=st["models"][0], system="", messages=[{"role": "user", "content": "Reply with the single word: pong"}], tools=[], timeout=20)
        return {"ok": True, "message": f"Provider responded ({'MOCK' if r.mock else 'real'}).", "text": r.text[:100]}
    except ProviderError as e:
        return {"ok": False, "message": e.message}


@router.get("/settings")
def settings():
    return get_settings()


class SettingsIn(BaseModel):
    max_steps: int | None = Field(default=None, ge=1, le=config.HARD_MAX_STEPS)
    max_run_seconds: int | None = Field(default=None, ge=1, le=config.HARD_MAX_RUN_SECONDS)
    max_agent_iterations: int | None = Field(default=None, ge=1, le=config.HARD_MAX_AGENT_ITERATIONS)
    require_approval_for_side_effects: bool | None = None
    loop_repeat_threshold: int | None = Field(default=None, ge=2, le=20)


@router.put("/settings", dependencies=admin_only)
def update_settings(body: SettingsIn):
    return put_settings({k: v for k, v in body.model_dump().items() if v is not None})


@router.get("/mock/outbox")
def outbox(limit: int = 50):
    with session() as s:
        rows = s.query(MockOutbox).order_by(MockOutbox.id.desc()).limit(limit).all()
        return [{"id": r.id, "run_id": r.run_id, "tool": r.tool, "idempotency_key": r.idempotency_key, "approval_id": r.approval_id, "payload": redact(r.payload), "created_at": iso(r.created_at)} for r in rows]


# ------------------------------------------------------------------ failure lab
class CaseIn(BaseModel):
    run_id: str
    name: str | None = Field(default=None, max_length=200)
    notes: str = ""
    expect: dict | None = None
    faults: list[dict] | None = None


def check_faults(faults):
    for f in faults or []:
        if f.get("kind") not in regression.FAULT_KINDS:
            raise err(422, "invalid_fault", f"Unsupported fault kind '{f.get('kind')}'. Supported: {', '.join(regression.FAULT_KINDS)}")
        if not 1 <= int(f.get("times", 1)) <= 10:
            raise err(422, "invalid_fault", "fault.times must be 1–10")


@router.post("/lab/cases", dependencies=admin)
def create_case(body: CaseIn):
    check_faults(body.faults)
    try:
        return regression.create_case(body.run_id, body.name, body.expect, body.notes, body.faults)
    except service.WorkflowError as e:
        raise wf_guard(e) from e


@router.get("/lab/cases")
def list_cases(workflow_id: str | None = None):
    with session() as s:
        q = s.query(RegressionCase)
        if workflow_id:
            q = q.filter_by(workflow_id=workflow_id)
        names = {w.id: w.name for w in s.query(Workflow).all()}
        vers = {v.id: v.version_no for v in s.query(WorkflowVersion).all()}
        out = []
        for c in q.order_by(RegressionCase.created_at.desc()).all():
            last = s.query(RegressionResult).filter_by(case_id=c.id).order_by(RegressionResult.created_at.desc()).first()
            out.append({"id": c.id, "name": c.name, "workflow_id": c.workflow_id, "workflow_name": names.get(c.workflow_id), "version_id": c.version_id, "version_no": vers.get(c.version_id),
                        "source_run_id": c.source_run_id, "fixture_count": len(c.fixtures), "faults": c.faults, "expect": c.expect, "created_at": iso(c.created_at),
                        "last_result": {"status": last.status, "version_no": vers.get(last.version_id), "run_id": last.run_id, "at": iso(last.created_at)} if last else None})
        return out


@router.get("/lab/cases/{cid}")
def get_case(cid: str):
    with session() as s:
        c = s.get(RegressionCase, cid)
        if not c:
            raise err(404, "not_found", "Case not found")
        results = s.query(RegressionResult).filter_by(case_id=cid).order_by(RegressionResult.created_at.desc()).limit(30).all()
        vers = {v.id: v.version_no for v in s.query(WorkflowVersion).filter_by(workflow_id=c.workflow_id).all()}
        return {**regression.case_dict(c), "results": [{"id": r.id, "version_id": r.version_id, "version_no": vers.get(r.version_id), "run_id": r.run_id, "status": r.status,
                                                        "checks": r.checks, "metrics": r.metrics, "created_at": iso(r.created_at)} for r in results]}


class CasePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    expect: dict | None = None
    faults: list[dict] | None = None


@router.patch("/lab/cases/{cid}", dependencies=admin)
def patch_case(cid: str, body: CasePatch):
    check_faults(body.faults)
    with session() as s:
        c = s.get(RegressionCase, cid)
        if not c:
            raise err(404, "not_found", "Case not found")
        if body.name is not None:
            c.name = body.name
        if body.notes is not None:
            c.notes = body.notes
        if body.expect is not None:
            c.expect = {**c.expect, **body.expect}
        if body.faults is not None:
            c.faults = body.faults
    return {"ok": True}


@router.delete("/lab/cases/{cid}", dependencies=admin)
def delete_case(cid: str):
    with session() as s:
        c = s.get(RegressionCase, cid)
        if not c:
            raise err(404, "not_found", "Case not found")
        s.query(RegressionResult).filter_by(case_id=cid).delete()
        s.delete(c)
    return {"ok": True}


class ReplayIn(BaseModel):
    version_id: str | None = None
    faults: list[dict] | None = None


@router.post("/lab/cases/{cid}/replay", dependencies=admin)
def replay_case(cid: str, body: ReplayIn):
    check_faults(body.faults)
    try:
        return regression.run_case(cid, body.version_id, body.faults)
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    except TimeoutError:
        raise err(504, "replay_timeout", "Replay did not finish in time") from None


class CompareIn(BaseModel):
    workflow_id: str
    version_a: str
    version_b: str
    case_ids: list[str] | None = None


@router.post("/lab/compare", dependencies=admin)
def compare(body: CompareIn):
    try:
        return regression.compare_versions(body.workflow_id, body.version_a, body.version_b, body.case_ids)
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    except TimeoutError:
        raise err(504, "replay_timeout", "A replay did not finish in time") from None


class DeterminismIn(BaseModel):
    runs: int = Field(default=5, ge=2, le=10)
    version_id: str | None = None


@router.post("/lab/cases/{cid}/determinism", dependencies=admin)
def determinism(cid: str, body: DeterminismIn):
    try:
        return regression.determinism_check(cid, body.runs, body.version_id)
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    except TimeoutError:
        raise err(504, "replay_timeout", "A replay did not finish in time") from None


class MatrixIn(BaseModel):
    workflow_id: str
    case_ids: list[str] | None = None


@router.post("/lab/matrix", dependencies=admin)
def matrix(body: MatrixIn):
    try:
        return regression.version_matrix(body.workflow_id, body.case_ids)
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    except TimeoutError:
        raise err(504, "replay_timeout", "A replay did not finish in time") from None


@router.get("/lab/cases/{cid}/export")
def export_case(cid: str):
    with session() as s:
        c = s.get(RegressionCase, cid)
        if not c:
            raise err(404, "not_found", "Case not found")
        d = regression.case_dict(c)
    return redact({"format": "flowpilot.regression-case/v1", "name": d["name"], "input": d["input"], "fixtures": d["fixtures"], "approvals": d["approvals"], "faults": d["faults"], "expect": d["expect"],
                   "notes": d["notes"], "sanitized": True, "note": "Secrets are redacted. Review fixtures for personal data before sharing."})


class CaseImport(BaseModel):
    workflow_id: str
    version_id: str | None = None
    data: dict


@router.post("/lab/cases/import", dependencies=admin)
def import_case(body: CaseImport):
    d = body.data
    if d.get("format") != "flowpilot.regression-case/v1" or not isinstance(d.get("fixtures"), list):
        raise err(400, "bad_format", "Expected a flowpilot.regression-case/v1 export")
    check_faults(d.get("faults"))
    for f in d["fixtures"]:
        if not isinstance(f, dict) or not {"node_id", "kind", "tool", "ordinal", "outcome"} <= set(f):
            raise err(400, "bad_fixture", "Malformed fixture entry")
    with session() as s:
        w = s.get(Workflow, body.workflow_id)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
    vid = body.version_id or service.snapshot_version(body.workflow_id, "")["id"]
    cid = new_id("case")
    with session() as s:
        s.add(RegressionCase(id=cid, name=str(d.get("name", "Imported case"))[:200], workflow_id=body.workflow_id, version_id=vid, input=redact(d.get("input", {})), fixtures=redact(d["fixtures"]),
                             approvals=d.get("approvals", []), faults=d.get("faults", []), expect=d.get("expect", {"status": "succeeded"}), notes=str(d.get("notes", "")), created_at=now()))
    return {"id": cid}


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard")
def dashboard():
    with session() as s:
        live = s.query(Run).filter(Run.mode == "live")
        counts = {k: v for k, v in s.query(Run.status, func.count()).filter(Run.mode == "live").group_by(Run.status).all()}
        recent = live.order_by(Run.created_at.desc()).limit(8).all()
        failures = live.filter(Run.status.in_(["failed", "timed_out"])).order_by(Run.created_at.desc()).limit(5).all()
        names = {w.id: w.name for w in s.query(Workflow).all()}
        latest_results = {}
        for r in s.query(RegressionResult).order_by(RegressionResult.created_at).all():
            latest_results[r.case_id] = r.status
        reg = {k: sum(1 for v in latest_results.values() if v == k) for k in ("passed", "failed", "inconclusive")}
        pend = s.query(ApprovalRequest).filter_by(status="pending").count()
        return {"run_counts": counts, "total_runs": sum(counts.values()), "workflow_count": s.query(Workflow).filter_by(deleted=False).count(),
                "recent_runs": [{**run_dict(r), "workflow_name": names.get(r.workflow_id)} for r in recent],
                "recent_failures": [{**run_dict(r), "workflow_name": names.get(r.workflow_id)} for r in failures],
                "pending_approvals": pend, "case_count": s.query(RegressionCase).count(), "regression_status": reg, "agent_count": s.query(Agent).count()}


# ------------------------------------------------------------------ evals & insights
class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workflow_id: str


class RowsIn(BaseModel):
    rows: list[dict] = Field(min_length=1, max_length=evals_mod.MAX_ROWS)


@router.get("/evals/datasets")
def list_datasets(workflow_id: str | None = None):
    with session() as s:
        q = s.query(EvalDataset)
        if workflow_id:
            q = q.filter_by(workflow_id=workflow_id)
        names = {w.id: w.name for w in s.query(Workflow).all()}
        out = []
        for d in q.order_by(EvalDataset.created_at.desc()).all():
            last = s.query(EvalRun).filter_by(dataset_id=d.id).order_by(EvalRun.created_at.desc()).first()
            out.append({"id": d.id, "name": d.name, "workflow_id": d.workflow_id, "workflow_name": names.get(d.workflow_id), "rows": s.query(EvalRow).filter_by(dataset_id=d.id).count(),
                        "last_run": {"id": last.id, "summary": last.summary, "at": iso(last.created_at)} if last else None})
        return out


@router.post("/evals/datasets", dependencies=admin)
def create_dataset(body: DatasetIn):
    with session() as s:
        w = s.get(Workflow, body.workflow_id)
        if not w or w.deleted:
            raise err(404, "not_found", "Workflow not found")
        did = new_id("ds")
        s.add(EvalDataset(id=did, name=body.name, workflow_id=body.workflow_id, created_at=now()))
    return {"id": did}


@router.get("/evals/datasets/{did}")
def get_dataset(did: str):
    with session() as s:
        d = s.get(EvalDataset, did)
        if not d:
            raise err(404, "not_found", "Dataset not found")
        rows = s.query(EvalRow).filter_by(dataset_id=did).all()
        runs = s.query(EvalRun).filter_by(dataset_id=did).order_by(EvalRun.created_at.desc()).limit(20).all()
        vers = {v.id: v.version_no for v in s.query(WorkflowVersion).filter_by(workflow_id=d.workflow_id).all()}
        return {"id": d.id, "name": d.name, "workflow_id": d.workflow_id, "rows": [{"id": r.id, "input": r.input, "expected": r.expected} for r in rows],
                "runs": [{"id": r.id, "version_id": r.version_id, "version_no": vers.get(r.version_id), "scorers": r.scorers, "judge": r.judge, "summary": r.summary, "at": iso(r.created_at)} for r in runs]}


@router.post("/evals/datasets/{did}/rows", dependencies=admin)
def add_rows(did: str, body: RowsIn):
    with session() as s:
        if not s.get(EvalDataset, did):
            raise err(404, "not_found", "Dataset not found")
        if s.query(EvalRow).filter_by(dataset_id=did).count() + len(body.rows) > evals_mod.MAX_ROWS:
            raise err(422, "too_large", f"A dataset holds at most {evals_mod.MAX_ROWS} rows")
        for r in body.rows:
            if not isinstance(r.get("input", {}), dict) or not isinstance(r.get("expected", {}), dict):
                raise err(422, "bad_row", "Each row needs object 'input' and 'expected'")
            s.add(EvalRow(id=new_id("row"), dataset_id=did, input=redact(r.get("input", {})), expected=r.get("expected", {})))
    return {"ok": True}


@router.delete("/evals/datasets/{did}", dependencies=admin)
def delete_dataset(did: str):
    with session() as s:
        if not s.get(EvalDataset, did):
            raise err(404, "not_found", "Dataset not found")
        for r in s.query(EvalRun).filter_by(dataset_id=did).all():
            s.query(EvalResult).filter_by(eval_run_id=r.id).delete()
        s.query(EvalRun).filter_by(dataset_id=did).delete()
        s.query(EvalRow).filter_by(dataset_id=did).delete()
        s.query(EvalDataset).filter_by(id=did).delete()
    return {"ok": True}


class EvalRunIn(BaseModel):
    dataset_id: str
    version_id: str
    scorers: list[str] = Field(min_length=1)
    judge: dict | None = None


@router.post("/evals/run", dependencies=admin)
def start_eval(body: EvalRunIn):
    try:
        return evals_mod.run_eval(body.dataset_id, body.version_id, body.scorers, body.judge)
    except service.WorkflowError as e:
        raise wf_guard(e) from e
    except TimeoutError:
        raise err(504, "eval_timeout", "A row did not finish in time") from None


@router.get("/evals/runs/{eid}")
def get_eval_run(eid: str):
    with session() as s:
        r = s.get(EvalRun, eid)
        if not r:
            raise err(404, "not_found", "Eval run not found")
        rows = {x.id: x for x in s.query(EvalRow).filter_by(dataset_id=r.dataset_id).all()}
        res = s.query(EvalResult).filter_by(eval_run_id=eid).all()
        v = s.get(WorkflowVersion, r.version_id)
        return {"id": r.id, "dataset_id": r.dataset_id, "version_no": v.version_no if v else None, "scorers": r.scorers, "judge": r.judge, "summary": r.summary,
                "results": [{"row_id": x.row_id, "run_id": x.run_id, "status": x.status, "scores": x.scores, "output": x.output, "duration_ms": x.duration_ms,
                             "input": rows[x.row_id].input if x.row_id in rows else None, "expected": rows[x.row_id].expected if x.row_id in rows else None} for x in res]}


@router.get("/insights")
def insights(days: int = Query(14, ge=1, le=90)):
    return insights_mod.compute(days)


@router.get("/audit")
def audit(limit: int = Query(100, le=500), offset: int = 0):
    with session() as s:
        total = s.query(AuditEntry).count()
        rows = s.query(AuditEntry).order_by(AuditEntry.id.desc()).offset(offset).limit(limit).all()
        return {"total": total, "entries": [{"id": r.id, "ts": iso(r.ts), "actor": r.actor, "method": r.method, "path": r.path, "status": r.status} for r in rows]}


# ------------------------------------------------------------------ memory
@router.get("/memory")
def list_memory(scope: str | None = None, key: str | None = None, limit: int = Query(100, le=500)):
    with session() as s:
        q = s.query(AgentMemory)
        if scope:
            q = q.filter_by(scope=scope)
        if key:
            q = q.filter_by(key=key)
        rows = q.order_by(AgentMemory.id.desc()).limit(limit).all()
        return [{"scope": r.scope, "key": r.key, "role": r.role, "content": r.content, "run_id": r.run_id, "at": iso(r.created_at)} for r in reversed(rows)]


@router.delete("/memory", dependencies=admin)
def clear_memory(scope: str, key: str):
    with session() as s:
        n = s.query(AgentMemory).filter_by(scope=scope, key=key).delete()
    return {"deleted": n}


# ------------------------------------------------------------------ credentials vault + OAuth
class CredIn(BaseModel):
    value: str = Field(min_length=1, max_length=20000)


CRED_NAME = r"^[A-Z][A-Z0-9_]{1,79}$"


@router.get("/credentials")
def list_credentials():
    return vault.listing()


@router.put("/credentials/{name}", dependencies=admin_only)
def put_credential(name: str, body: CredIn):
    import re as _re
    if not _re.fullmatch(CRED_NAME, name):
        raise err(422, "bad_name", "Use an UPPER_SNAKE_CASE name, e.g. SLACK_WEBHOOK_URL")
    vault.put(name, body.value)
    return {"ok": True}


@router.delete("/credentials/{name}", dependencies=admin_only)
def delete_credential(name: str):
    if not vault.delete(name):
        raise err(404, "not_found", "Credential not found")
    vault.delete(name + "__REFRESH")
    return {"ok": True}


class OAuthAppIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    auth_url: str
    token_url: str
    client_id: str
    client_secret: str = ""
    scopes: str = ""
    credential_name: str = Field(pattern=CRED_NAME)


@router.get("/oauth/apps")
def oauth_apps():
    with session() as s:
        return [{"name": a.name, "auth_url": a.auth_url, "token_url": a.token_url, "client_id": a.client_id, "scopes": a.scopes, "credential_name": a.credential_name,
                 "connected": vault.configured(a.credential_name)} for a in s.query(OAuthApp).order_by(OAuthApp.name).all()]


@router.post("/oauth/apps", dependencies=admin_only)
def create_oauth_app(body: OAuthAppIn):
    for u in (body.auth_url, body.token_url):
        if not u.startswith(("https://", "http://")):
            raise err(422, "bad_url", "auth_url/token_url must be http(s) URLs")
    ref = body.credential_name + "__CLIENT_SECRET"
    if body.client_secret:
        vault.put(ref, body.client_secret)
    with session() as s:
        s.merge(OAuthApp(name=body.name, auth_url=body.auth_url, token_url=body.token_url, client_id=body.client_id, client_secret_ref=ref, scopes=body.scopes, credential_name=body.credential_name))
    return {"ok": True, "redirect_uri": oauth_mod.redirect_uri()}


@router.delete("/oauth/apps/{name}", dependencies=admin_only)
def delete_oauth_app(name: str):
    with session() as s:
        a = s.get(OAuthApp, name)
        if not a:
            raise err(404, "not_found", "OAuth app not found")
        cn = a.credential_name
        s.delete(a)
    for n in (cn, cn + "__REFRESH", cn + "__CLIENT_SECRET"):
        vault.delete(n)
    return {"ok": True}


@router.post("/oauth/apps/{name}/start", dependencies=admin_only)
def oauth_start(name: str):
    try:
        return {"url": oauth_mod.start(name), "redirect_uri": oauth_mod.redirect_uri()}
    except KeyError:
        raise err(404, "not_found", "OAuth app not found") from None


@router.get("/oauth/callback")
def oauth_callback(state: str = "", code: str = "", error: str = ""):
    from fastapi.responses import RedirectResponse
    if error or not code:
        return RedirectResponse("/integrations?oauth_error=" + (error or "missing_code")[:60])
    try:
        app_name = oauth_mod.callback(state, code)
    except Exception as e:  # noqa: BLE001 — never leak provider responses into the URL
        return RedirectResponse("/integrations?oauth_error=" + type(e).__name__)
    return RedirectResponse(f"/integrations?connected={app_name}")


# ------------------------------------------------------------------ team mode: auth + users
_fails: dict[str, list[float]] = {}


class SetupIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str = Field(max_length=200)


@router.get("/auth/status")
def auth_status(request: Request):
    u = users.resolve(request)
    return {"mode": users.get_mode(), "has_users": users.user_count() > 0, "user": {k: u[k] for k in ("id", "email", "name", "role")} if u else None, "csrf": u["csrf"] if u else None}


@router.post("/auth/setup", dependencies=admin)
def auth_setup(body: SetupIn):
    """Create the first admin and switch to team mode. Needs the local admin token, and only works while there are no users."""
    if users.user_count() > 0:
        raise err(409, "already_setup", "Users already exist")
    uid = users.create_user(body.email, body.name, body.password, "admin")
    users.set_mode("users")
    return {"id": uid, "mode": "users"}


@router.post("/auth/login")
def auth_login(body: LoginIn, request: Request):
    import time as _t
    from fastapi.responses import JSONResponse
    if not auth.host_is_local(request) and not os.environ.get("FLOWPILOT_ALLOWED_HOSTS"):
        raise err(403, "forbidden", "Host not allowed")
    key = body.email.lower().strip() + "|" + (request.client.host if request.client else "")
    recent = [t for t in _fails.get(key, []) if _t.time() - t < 60]
    if len(recent) >= 5:
        raise err(429, "too_many_attempts", "Too many failed sign-ins. Try again in a minute.")
    res = users.login(body.email, body.password)
    if not res:
        _fails[key] = recent + [_t.time()]
        raise err(401, "bad_credentials", "Invalid email or password")
    _fails.pop(key, None)
    token, csrf, user = res
    resp = JSONResponse({"user": user, "csrf": csrf})
    resp.set_cookie(users.COOKIE, token, httponly=True, samesite="strict", max_age=users.SESSION_HOURS * 3600, secure=request.url.scheme == "https", path="/")
    return resp


@router.post("/auth/logout")
def auth_logout(request: Request):
    from fastapi.responses import JSONResponse
    tok = request.cookies.get(users.COOKIE)
    if tok:
        users.logout(tok)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(users.COOKIE, path="/")
    return resp


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=200)
    role: str = Field(pattern="^(viewer|editor|admin)$")


class UserPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(viewer|editor|admin)$")
    active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)


@router.get("/users", dependencies=admin_only)
def list_users():
    from backend.db.models import User
    with session() as s:
        return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": u.active, "created_at": iso(u.created_at)} for u in s.query(User).order_by(User.created_at).all()]


@router.post("/users", dependencies=admin_only)
def create_user(body: UserIn):
    from sqlalchemy.exc import IntegrityError
    try:
        return {"id": users.create_user(body.email, body.name, body.password, body.role)}
    except IntegrityError:
        raise err(409, "exists", "A user with this email already exists") from None


@router.patch("/users/{uid}", dependencies=admin_only)
def patch_user(uid: str, body: UserPatch):
    from backend.db.models import SessionRow, User
    with session() as s:
        u = s.get(User, uid)
        if not u:
            raise err(404, "not_found", "User not found")
        admins = s.query(User).filter_by(role="admin", active=True).count()
        demoting = u.role == "admin" and u.active and ((body.role and body.role != "admin") or body.active is False)
        if demoting and admins <= 1:
            raise err(409, "last_admin", "At least one active admin is required")
        if body.role:
            u.role = body.role
        if body.active is not None:
            u.active = body.active
        if body.password:
            u.pw_hash = users.hash_password(body.password)
        if body.active is False or body.password or body.role:
            s.query(SessionRow).filter_by(user_id=uid).delete()  # force re-login after any privilege/credential change
    return {"ok": True}


@router.post("/auth/mode", dependencies=admin_only)
def set_auth_mode(body: dict):
    if body.get("mode") not in ("local", "users"):
        raise err(422, "bad_mode", "mode must be 'local' or 'users'")
    if body["mode"] == "users" and users.user_count() == 0:
        raise err(409, "no_users", "Create the first admin with /auth/setup first")
    users.set_mode(body["mode"])
    return {"mode": body["mode"]}
