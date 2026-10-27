# Progress (resume point)

## Verified (actually executed)
* Backend: `.venv/bin/python -m pytest tests -q` → 48 passed (incl. webhook dedupe, safety linter, effects ledger, determinism, version bisect, cron/scheduler, publish API, knowledge RAG, connectors, populated-DB migration) (engine, retry/reconcile, approvals, cancel, loops, redaction, SSRF, webhook, versions, restart recovery, Failure Lab).
* Browser E2E: `cd frontend && npx playwright test` → 11 passed (I knowledge/schedule/publish; H differentiators; A create/run/refresh, A2 drag-connect + validation, B retry history, C unknown-outcome reconcile, D reject/approve, D2 cancel, E failure→case→replay→compare, G token/secrets, error states).
* UI redesign ("agent flight deck" HUD look) done by an Opus subagent; after it, I re-ran tsc (clean), pytest (35 passed) and Playwright (9 passed) myself and inspected `artifacts/screenshots/01-overview.png` and `03-run-live.png`. The remaining screenshots (02, 04–09) were produced by the agent; I have not individually reviewed them.
* Fixed after review: naive SQLite timestamps (elapsed/ago were off by the timezone offset) via a UTC-aware column type.

## Implemented but NOT verified
* Anthropic provider adapter (no API key available; never called live).
* http.request real network path beyond SSRF blocking (no outbound test).
* Restart recovery is verified in pytest only (simulated crash state), not via a real process kill / browser.
* Responsive/mobile layout, accessibility audit, Lab/Agents/Integrations/Settings pages' visual review (pending the design pass).

## Not implemented
Agent memory, parallel branches, cost estimation, multi-user auth, code node (by design), Postgres run, hosted deploy.

## Notes for the next session
* Ports: API 8765, web 5173; E2E uses 8766/5174 with a temp DB. Port 8000 belongs to an unrelated project.
* Model allocation: all engine/backend work ran on one model; only the UI redesign was delegated to an Opus agent.
* Docs: ARCHITECTURE, EXECUTION_MODEL, SECURITY, DECISIONS, COMPETITIVE_VALIDATION (not researched).

## Known visual issues
Wide run graphs render at ~0.6 zoom; the graph sits below the fold while an approval card is showing; the hero radar decoration is faint.

## Competitive research (done 2026-10-22)
See `docs/COMPETITIVE_VALIDATION.md`. Added: webhook event dedupe (migration 0002), side-effect safety linter, per-run side-effect ledger, determinism check, version bisect matrix. Verified by pytest + E2E H; Compare matrix viewed in the browser. Ledger and determinism panels verified by E2E only (not visually inspected by me).

## All-in-one tranche 1 (done)
Schedule/cron trigger + scheduler thread (fires once per UTC minute, no back-fill), publish-as-API, knowledge bases + `knowledge.search`, connector framework (Slack/Discord/Telegram/GitHub/SMTP — real-service calls unverified), migration 0003. Bugs found by these tests: HTTP tool crashed on every real response (`httpx` response used as a context manager) — fixed; SQLite batch migration tripped foreign keys on a populated DB and left a half-applied schema — fixed (FK-safe upgrade + regression test).
## Not yet built for 'all-in-one' (next tranches)
LLM-as-judge / scored evals and datasets; production tracing analytics and cost dashboards; sub-workflows and loop-over-items fan-out/parallel branches; more connectors (Google Sheets/Drive, Notion, Airtable, Stripe, Postgres/MySQL queries, S3); OAuth credential vault; chat UI / embeddable widget; multi-user auth, roles, audit log; Postgres + multi-process workers/queue; embeddings-based retrieval; agent memory; marketplace/templates library.

## All-in-one tranche 2 (done)
Scored evals (datasets, exact/contains/regex/json_subset/judge scorers; mock judge is a labelled keyword heuristic; evals never bypass approvals) and Insights (runs/day, success rate, p50/p95, error codes, tool failure rates, slow nodes; tokens only if provider-reported; cost never estimated). Migration 0004. Verified: pytest 51 passed, Playwright 12 passed. Not visually inspected by me: Evals and Insights pages. Real-provider judge untested.

## All-in-one tranche 3 (done)
Templates gallery (6 runnable templates incl. support-RAG, incident router with dedupe, scheduled digest) and an audit log (admin method+path+status, no bodies; migration 0005). Verified: pytest 53 passed, Playwright 13 passed; dev DB upgraded to 0005 with data intact. Templates/Settings audit UI not visually inspected by me.
Still not built: sub-workflows & parallel branches, more connectors (Sheets/Notion/Airtable/Stripe/DBs/S3), OAuth vault, chat widget, multi-user auth/roles, Postgres + multi-process workers, embeddings retrieval, agent memory.

## All-in-one tranche 4 (done): sub-workflows + parallel map
`subworkflow` node (single call or map over ≤20 items in parallel). Children are real persisted runs (`parent_run_id`, `parent_key`, `depth`; migration 0006); parent enters `waiting_for_child` and releases its worker (no deadlock); wake-up is compare-and-set so it resumes exactly once; lost-wakeup guard; cancel cascades; recursion rejected, depth ≤3; fixture replay uses recorded child results and never starts children. Verified: pytest 59 passed, Playwright 14 passed. Not verified: crash-restart mid fan-out with a real process kill (the resume path reuses the same checkpoint logic and is exercised by the wake/exactly-once test only); child-runs panel and node form not visually inspected by me.
Still not built: more connectors, OAuth vault, chat widget, multi-user auth/roles, Postgres + multi-process workers, embeddings retrieval, agent memory.
