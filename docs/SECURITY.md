# Security notes (local single-user build)

**Not production-secure and not multi-tenant.** Assumptions: one trusted user on a trusted machine, backend bound to localhost.

| Area | Implementation |
|---|---|
| Authorization | State-changing endpoints require `X-FlowPilot-Token`; `/api/session` issues it only to localhost Host+Origin requests. CORS limited to localhost origins; non-local `Host` headers are rejected (DNS rebinding). Not user authentication. |
| Secrets | Never stored. Providers read API keys from env vars (SecretReference stores only the variable *name*). `redact()` masks sensitive keys/values before events, steps, tool calls, fixtures, exports and API output. |
| SSRF | `http.request`: http/https only, ports 80/443/8080/8443, no URL credentials, DNS resolved and **every address validated**, connection made to the validated IP (SNI/Host preserved), redirects re-validated (max 3), response size and time capped. Private/loopback/link-local/metadata ranges blocked. |
| Code execution | None. Transform/condition use an AST-whitelisted expression evaluator (no calls except a few pure builtins/str methods, no dunder access, size limits). |
| Webhooks | Per-workflow token (stored as SHA-256), constant-time compare, 256 KB cap, JSON object only, input-schema validation. |
| Approvals | Backend-enforced, bound to args hash, single decision, run must be waiting. |
| Resource limits | step/time/iteration/retry caps, payload caps, workflow size caps. |
| Prompt injection | Tool output is data: agents can only call tools allow-listed on the node; side-effect tools need approval regardless of model output. No guarantee against model manipulation within allowed tools. |
| Fixtures/exports | Redacted, but may contain personal data from tool results – review before sharing. |
Known gaps: no rate limiting on the API, no per-user isolation, SQLite file is unencrypted, the worker runs in the API process, the real-provider adapter is untested against the live API.
