# FlowPilot architecture

Local-first, single-process app: **React/Vite SPA** → **FastAPI** → **SQLite (WAL)**, with a DB-polling **worker pool** inside the API process.

```
frontend/            React + TS + Tailwind v4 + @xyflow/react (Studio, run views, Failure Lab)
backend/api/         REST + SSE routes (routes.py)
backend/workflows/   definition schema, static validation, safe expression engine, versions service, examples
backend/execution/   state machine, engine (graph walk), node handlers, runner (tools/LLM guard), worker, event store
backend/providers/   LLM adapter interface; mock (deterministic) + anthropic
backend/tools/       tool registry: mock.*, http.request (SSRF guarded)
backend/replay/      fixtures, regression cases, fault injection, version comparison
backend/security/    redaction, SSRF guard, local admin token
backend/db/          SQLAlchemy models, Alembic migrations (0001), session helpers
```

## Key decisions
* **Durability = the database.** `runs`, `run_steps`, `execution_events`, `tool_calls`, `approval_requests` are written at every step. The worker only *claims* queued runs (CAS update), it never holds the source of truth.
* **Deterministic re-walk.** To resume (after approval, crash, restart) the engine re-walks the graph from Start. Steps whose record is `succeeded` are restored from the DB and *not re-executed*. Inside a step, every tool/LLM call is checkpointed by `(run, node, visit, seq)`.
* **Versions are immutable snapshots.** The editable *draft* lives on `workflows`; `workflow_versions` rows are never updated. A run references a version id. Agent configs are embedded in the version when it is created.
* **Provider/tool adapters** are plain registries; the engine knows only `LLMResult` and `ToolSpec`.
* **Events**: per-run monotonic `seq`; SSE streams `id: <seq>`; reconnect uses `Last-Event-ID`/`?after=`; the UI also backfills via REST.
* **No Redis / microservices.** Single process; see Limitations for the multi-process story.

## Data model (tables)
workflows, workflow_versions, agents, runs, run_steps, execution_events, tool_calls (kind `tool`|`llm`), approval_requests (kind `gate`|`node`|`reconcile`), regression_cases, regression_results, secret_references (env-var *names* only), provider_configurations, settings, mock_outbox (the mock external world).

## API (prefix `/api`)
GET reads are open on localhost; every state-changing call needs `X-FlowPilot-Token`. See `backend/api/routes.py` – workflows (CRUD/versions/diff/export/import), `/examples`, `/runs` (+events, SSE, cancel, rerun, replay), `/approvals`, `/agents`, `/providers`, `/settings`, `/lab/*`, `/hooks/{workflow}` (webhook), `/dashboard`.
