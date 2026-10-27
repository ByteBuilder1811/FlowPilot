# FlowPilot

Visual AI-workflow automation with a reliability engine: **build → execute → inspect → recover → replay → test → improve.** Runs fully locally with a deterministic mock model; no paid credentials needed.

## Run it
```bash
# backend (Python 3.13)
python3.13 -m venv .venv && .venv/bin/pip install fastapi "uvicorn[standard]" sqlalchemy alembic pydantic httpx pytest pytest-asyncio
.venv/bin/python -m uvicorn backend.main:app --port 8765      # applies migrations on start
# frontend
cd frontend && npm install && npm run dev                     # http://localhost:5173 (proxies /api -> :8765)
```
Production-style: `cd frontend && npm run build` – the backend then serves `frontend/dist`.

Environment (all optional): `FLOWPILOT_DATA_DIR`, `FLOWPILOT_DB_URL`, `FLOWPILOT_API_TOKEN`, `ANTHROPIC_API_KEY` (real provider), `FLOWPILOT_ALLOWED_HOSTS`, `FLOWPILOT_HTTP_ALLOW_PRIVATE_HOSTS` (dev only).

## What's in the box
Studio (visual builder) · Agents · **Knowledge** (local BM25 RAG with citations) · Runs/live execution center · Approvals & reconciliation · Failure Lab (cases, replay, determinism proof, fault injection) · Compare + version bisect · **Triggers**: manual, webhook (token + event dedupe), **schedule (cron, UTC)** · **Publish any workflow as an authenticated API** (`POST /api/v1/workflows/{id}/invoke`) · **Connectors**: Slack, Discord, Telegram, GitHub issues, SMTP email, generic HTTP (all approval-gated; credentials only via environment variables: `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, `SMTP_HOST`/`SMTP_FROM`/`SMTP_USER`/`SMTP_PASSWORD`).
Connectors other than the local test of Slack-style webhook posting have **not** been exercised against the real services.

## Tests
```bash
.venv/bin/python -m pytest tests -q          # backend: engine, reliability, security, lab
cd frontend && npx playwright test           # browser E2E on an isolated backend+DB (ports 8766/5174)
```

## 2-minute demo journey
1. Overview → **Resilient agent with failure (sample)** → Run. The flaky lookup fails and the 1-attempt policy stops the run.
2. Open the run → read the retry decision → **Save as regression case**.
3. Failure Lab → **Replay case** (fixture replay: nothing real executes; still failing).
4. Studio → select the *Retry policy* node → attempts = 3 → Versions → **Save as version**.
5. Compare → v1 vs v2 → the case flips to **Improvement**, with the exact config diff.
6. Run v2 live: the email step pauses for **approval**.

Docs: `docs/` (architecture, execution model, security, decisions, progress).
