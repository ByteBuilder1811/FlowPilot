# Execution model

## Run states
`queued → running → {waiting_for_approval, retrying, succeeded, failed, cancelled, timed_out}`. Terminal states are final (`backend/execution/state.py`; every transition is validated and atomically applied in `store.transition`). `running → queued` is used only by crash recovery and after an approval decision.

## Budgets
max steps, active-seconds (approval wait time excluded), agent iterations, loop iterations (strict), retries, backoff cap. Workflow policy may only be stricter than workspace settings; both are capped by hard limits in `config.py`.

## Retry decision (`runner.decide_retry`)
| situation | decision |
|---|---|
| permanent error | final (no retry) |
| side effect with **unknown outcome** | retry only with an idempotency key or explicit `safe_to_retry`; otherwise **reconcile** (run pauses, operator chooses *mark done / retry / fail*) – regardless of attempts left |
| transient, call did not take effect, or read-only | retry with exponential backoff (bounded, no jitter so replays are deterministic) while attempts remain |
| budget exhausted | final, reason recorded |
Every decision and reason is stored in the step's `attempts` and emitted as events.

## Approvals
Enforced in the tool layer: a side-effecting call (non-GET HTTP, `side_effect` tools, or nodes flagged `requires_approval`) cannot execute without an `approved` row bound to the **exact tool+arguments hash**; the check is repeated immediately before execution. Human Approval nodes use the same table (`kind=node`).

## Crash / restart
On startup runs left `running|retrying` are re-queued. Completed steps restore from checkpoints. A tool call recorded as `started` without a result is treated as *outcome unknown* → idempotency-key retry or reconciliation – never a blind repeat. Delays restart from zero after a restart (documented limitation).

## Loop detection
Signals: (1) per-loop iteration budget (strict), (2) identical tool+arguments repeated ≥ N times within one agent step, (3) agent iteration budget, (4) global step budget. Each emits a diagnostic (`loop.detected`). No cross-iteration "state hash" heuristic yet.

## Replay
*Fixture replay* (`mode=fixture_replay`): tool/LLM results come from recorded fixtures; side-effect tools never execute (unrecorded ones return a labelled `simulated` result); pure deterministic tools may compute; anything else without a fixture ⇒ `fixture_miss` ⇒ **inconclusive**. Recorded approval decisions are replayed; unrecorded gates follow the case's `approval_default`. *Live rerun* calls real tools again and is labelled as such.
