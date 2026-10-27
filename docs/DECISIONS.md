# Decisions
1. **Python 3.13 venv** (3.14's `ensurepip` is broken in this environment).
2. **SQLite WAL + SQLAlchemy + Alembic** (explicit migration `0001`); PostgreSQL-compatible types only.
3. **DB-polling workers in-process** instead of a queue service – durable and simple to run; one process only.
4. **Retry policy** is a per-node setting *and* a wired "Retry / Recovery Policy" node that applies to the next node. Recovery = optional `error` output edge.
5. **Webhook trigger** = Start node option with token auth (safe on localhost; public exposure needs a tunnel the user controls).
6. **Transform node** instead of a code node: declarative operations + sandboxed expressions. No arbitrary code.
7. **Anthropic** chosen as the first real provider (adapter via HTTPS, no SDK). Cost is never computed (no reliable pricing source); tokens only when the provider reports them.
8. **Ports**: API 8765 (8000 was occupied by an unrelated process), web 5173.
9. **Model allocation**: the work in this session ran on a single model; no per-agent Opus/Sonnet split was performed for the engine (see PROGRESS.md for the one Opus design pass).
