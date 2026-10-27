# Competitive validation

Method: public documentation and public community posts only, read on 2026-10-22 (no product was installed or exercised; no proprietary code or private details were studied). "Not found" means *not found in the pages I read* — it is **not** a claim the product lacks it. Fetched pages that contained instructions aimed at AI agents were ignored.

## 1. Confirmed capabilities (from official docs I read)
| Product | What the docs I read confirm |
|---|---|
| **n8n** | Error workflows (Error Trigger; error data includes execution id, last node, `retryOf`), Stop-and-Error node ([error handling](https://docs.n8n.io/flow-logic/error-handling/)). Execution history, manual vs production executions, copying a past execution's data into the editor for debugging, data redaction ([executions](https://docs.n8n.io/workflows/executions/)). Evaluations: datasets of test cases, light and metric-based evals, usable for regression testing; page doesn't mention version comparison or fixture replay ([evaluations](https://docs.n8n.io/advanced-ai/evaluations/overview/)). |
| **Make** | Error directives: Break (stores an incomplete execution, optional auto-retry), Commit, Rollback (only for modules that support transactions), Ignore; Rollback is the default when no handler is set ([directives](https://www.help.make.com/en/help/errors/directives-for-error-handling), via search summary). |
| **Dify** | Human Input node: form with editable fields and action buttons, 3-day default timeout, timeout branch, email or web delivery; request closes after first response; not available for Trigger-started workflows; email links need no account ([human input](https://enterprise-docs.dify.ai/en/3.10.x/use/nodes/human-input)). Error-handling branches for LLM/HTTP/Tool nodes ([blog](https://dify.ai/blog/boost-ai-workflow-resilience-with-error-handling)); retry settings documented for tool nodes. |
| **Langfuse** | Nested tracing, datasets (from production traces), prompt versioning, experiments with side-by-side run comparison ([changelog](https://langfuse.com/changelog)). |
| **Temporal** | Durable replay from event history; non-determinism errors on replay; activities retry by default (1 s initial, ×2, 100 s cap, unlimited attempts); non-retryable error types; activities are expected to be idempotent ([retry policies](https://docs.temporal.io/encyclopedia/retry-policies)). |
| **Promptfoo** | CLI/library for evals and red-teaming, matrix comparison of prompts, caching, CI use ([intro](https://www.promptfoo.dev/docs/intro)). I found no deeper docs. |

## 2. Publicly reported pain points (practitioner posts — opinion, may be fixed)
* n8n users report duplicate side effects when webhook providers re-deliver events, and warn that blind retry after a timeout risks duplicates; the common workaround is a hand-built idempotency gate and the observation that "seen" must differ from "completed" ([thread](https://community.n8n.io/t/stop-duplicate-side-effects-on-webhook-retries/315807), [thread](https://community.n8n.io/t/shopify-n8n-5-tests-for-duplicate-webhooks-and-failed-run-recovery/315461)).
* Make Rollback can leave inconsistencies when not every module supports transactions (third-party write-up).
* Dify's Human Input docs don't describe binding an approval to a specific action's arguments or tamper protection (absence in docs).

## 3. Gaps I could not find documented → what FlowPilot now implements (and how it is verified)
| Gap hypothesis | FlowPilot feature | Verified by |
|---|---|---|
| Unknown-outcome side effects get retried or silently fail | Policy engine: unknown outcome ⇒ pause for **reconciliation** (mark done / retry / fail); idempotency-key retries are the only automatic path | pytest + E2E C |
| Approvals not bound to the exact action | Approval bound to hash of tool+arguments, re-verified at execution | pytest, E2E D |
| Webhook re-delivery duplicates work; "seen" vs "done" conflated | **Built-in event dedupe**: `Idempotency-Key` header or Start-node dedupe key; in-flight/succeeded ⇒ existing run returned; failed ⇒ re-attempt allowed (`retry_of`) | `test_webhook_dedupe_*`, `test_failed_delivery_*` |
| Duplicate-risk is discovered in production | **Side-effect safety linter** warns in the Studio (unsafe `safe_to_retry`, retries without idempotency key, webhook + side effects without dedupe) | `test_safety_linter_*` |
| "What did this run really do to the outside world?" | **Side-effect ledger** per run: executed / unknown / reconciled / simulated / not executed, approval + idempotency key per entry | `test_effects_ledger_*`, `test_replay_ledger_*`, E2E H |
| Is a regression case actually deterministic? | **Determinism check**: replay N× and compare fingerprints of steps/tool results/outputs | `test_determinism_check`, E2E H |
| Which saved version broke this case? | **Version matrix / bisect**: every case × every version, names the first pass→fail version with the exact config diff | `test_version_matrix_*`, E2E H |
| Failure → test conversion cost | One click from a failed run to a fixture-backed case; replay never executes side effects (unrecorded ones are labelled *simulated*) | pytest, E2E E |

## 4. Not claimed / not done
* No claim that any competitor lacks these; several may offer equivalents not covered by the pages I read.
* FlowPilot lacks, relative to these products: Temporal-grade scale/durability across processes, Langfuse-style production tracing/analytics and LLM-judge evals, Promptfoo red-teaming, n8n/Make integration breadth, Dify RAG/app publishing, Make-style visual error routes beyond a single error output.
* Superiority must not be advertised until the benchmark below is run.

## 5. Benchmark plan (equivalent flaky-tool + email workflow in each product)
| Question | Measure |
|---|---|
| Time to diagnose a failed run | seconds from opening the run to stating cause + retry decision (3 testers) |
| Steps to convert failure → regression test | clicks from failed run to saved replayable case |
| Deterministic replay | same case 10× → identical fingerprints? |
| Duplicate side-effect risk | inject a timeout *after* the side effect; count external effects after default recovery |
| Attribute a regression to a change | can the user see which config field changed and which version flipped which case? |
| Webhook re-delivery | send the same event 5×; count executions and external effects |
Record versions, dates and raw results; link docs; keep confirmed capability / reported pain / hypothesis separate.
