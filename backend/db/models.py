from datetime import datetime

from datetime import timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from backend.db.session import Base


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo; always hand back aware UTC datetimes so API timestamps carry an offset."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _ts(**kw):
    return mapped_column(UTCDateTime(), **kw)


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    draft: Mapped[dict] = mapped_column(JSON)  # editable working copy; versions are immutable snapshots
    webhook_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publish_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_fired_minute: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSON)  # agent configs are embedded so history never changes meaning
    content_hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("workflow_id", "version_no"),)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("workflow_versions.id"))
    status: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="live")  # live | fixture_replay
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)  # fixtures, faults, approval decisions for replay
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    parent_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires: Mapped[datetime | None] = _ts(nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = _ts(nullable=True)
    completed_at: Mapped[datetime | None] = _ts(nullable=True)


class RunStep(Base):
    __tablename__ = "run_steps"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(32))
    visit: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24))  # running|succeeded|failed|waiting|retrying|skipped
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    handle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ctx_patch: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = _ts(nullable=True)
    __table_args__ = (UniqueConstraint("run_id", "node_id", "visit"),)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(48))
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("run_id", "seq"), Index("ix_events_run_seq", "run_id", "seq"))


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8), default="tool")  # tool | llm
    tool: Mapped[str] = mapped_column(String(80))
    visit: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)  # nth inner call within the step execution (incl. retries): step-internal checkpoint key
    ordinal: Mapped[int] = mapped_column(Integer, default=0)  # nth call of this tool by this node within the run: fixture key
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    args_hash: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(16))  # started | ok | error | unknown
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    side_effect: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | fixture | fault
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = _ts(nullable=True)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    visit: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16))  # gate | node | reconcile
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    proposed: Mapped[dict] = mapped_column(JSON, default=dict)
    args_hash: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = _ts(nullable=True)


class RegressionCase(Base):
    __tablename__ = "regression_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("workflow_versions.id"))
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    fixtures: Mapped[list] = mapped_column(JSON, default=list)
    approvals: Mapped[list] = mapped_column(JSON, default=list)
    faults: Mapped[list] = mapped_column(JSON, default=list)
    expect: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()


class RegressionResult(Base):
    __tablename__ = "regression_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("regression_cases.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("workflow_versions.id"))
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # passed | failed | inconclusive
    checks: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class SecretReference(Base):
    """Secrets are never stored: a reference names the environment variable that holds the value."""
    __tablename__ = "secret_references"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    env_var: Mapped[str] = mapped_column(String(80))


class ProviderConfiguration(Base):
    __tablename__ = "provider_configurations"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = _ts()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class MockOutbox(Base):
    """The 'external world' of the mock side-effect tools, so tests/UI can prove whether an action really happened."""
    __tablename__ = "mock_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class IdempotencyKey(Base):
    """Webhook event dedupe: one row per (workflow, event key). 'seen' is not 'done': failed runs may be re-attempted."""
    __tablename__ = "idempotency_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    key: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("workflow_id", "key"),)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[str] = mapped_column(String(16), default="lexical", server_default="lexical")  # lexical | ngram | remote
    created_at: Mapped[datetime] = _ts()


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = _ts()


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("knowledge_docs.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    vec: Mapped[list | None] = mapped_column(JSON, nullable=True)


class EvalDataset(Base):
    __tablename__ = "eval_datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    created_at: Mapped[datetime] = _ts()


class EvalRow(Base):
    __tablename__ = "eval_rows"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("eval_datasets.id"), index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    expected: Mapped[dict] = mapped_column(JSON, default=dict)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("eval_datasets.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("workflow_versions.id"))
    scorers: Mapped[list] = mapped_column(JSON, default=list)
    judge: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class EvalResult(Base):
    __tablename__ = "eval_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    row_id: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # passed | failed | errored | blocked
    scores: Mapped[list] = mapped_column(JSON, default=list)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuditEntry(Base):
    """Who changed what. Method + path + status only — request bodies are never stored (they may contain secrets)."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _ts()
    actor: Mapped[str] = mapped_column(String(64), default="local-admin")
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(300))
    status: Mapped[int] = mapped_column(Integer)


class AgentMemory(Base):
    """Conversation memory for LLM nodes/agents, scoped and keyed (e.g. per chat session). Idempotent per step."""
    __tablename__ = "agent_memory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(32))
    node_id: Mapped[str] = mapped_column(String(64))
    visit: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("run_id", "node_id", "visit", "role"),)


class Credential(Base):
    """Encrypted credential vault. `name` is the env-var-style name connectors ask for (e.g. SLACK_WEBHOOK_URL)."""
    __tablename__ = "credentials"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), default="secret")  # secret | oauth
    ciphertext: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # oauth: app, expires_at, scopes. Never plaintext secrets.
    updated_at: Mapped[datetime] = _ts()


class OAuthApp(Base):
    __tablename__ = "oauth_apps"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    auth_url: Mapped[str] = mapped_column(String(500))
    token_url: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(300))
    client_secret_ref: Mapped[str] = mapped_column(String(80))  # vault credential name holding the client secret
    scopes: Mapped[str] = mapped_column(String(500), default="")
    credential_name: Mapped[str] = mapped_column(String(80))  # where the access token is stored in the vault


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    app: Mapped[str] = mapped_column(String(80))
    verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = _ts()


class WorkerRecord(Base):
    """Liveness registry: a worker process heartbeats here; runs owned by a dead worker are recovered."""
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    host: Mapped[str] = mapped_column(String(120), default="")
    pid: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = _ts()
    last_seen: Mapped[datetime] = _ts()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(10), default="viewer")  # viewer | editor | admin
    pw_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class SessionRow(Base):
    """Server-side session; only a SHA-256 of the cookie token is stored."""
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = _ts()
