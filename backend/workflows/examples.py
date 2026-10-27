"""Three executable sample workflows. They run entirely on mock tools/providers (no credentials, no network)."""


def N(id_, type_, x, y, label, **config):
    return {"id": id_, "type": type_, "position": {"x": x, "y": y}, "data": {"label": label, "config": config}}


def E(s, t, h="out"):
    return {"id": f"e_{s}_{h}_{t}", "source": s, "sourceHandle": h, "target": t}


RESEARCH = {
    "format": "flowpilot.workflow/v1", "policy": {},
    "nodes": [
        N("start", "start", 40, 160, "Topic", trigger="manual", input_schema={"required": ["topic"], "properties": {"topic": {"type": "string"}}}),
        N("researcher", "llm", 300, 120, "Research agent", provider="mock", model="mock-agent-1", tools=["mock.search"], max_iterations=4,
          system="You are a careful research assistant. Cite sources.",
          prompt='Research this topic and summarize with citations: {{ input.topic }}\n[[tool:mock.search {"query": "{{ input.topic }}", "limit": 3}]]'),
        N("format", "transform", 590, 160, "Format report", op="template", template="# {{ input.topic }}\n\n{{ nodes.researcher.text }}"),
        N("end", "end", 860, 160, "Report", output="{{ nodes.format.value }}"),
    ],
    "edges": [E("start", "researcher"), E("researcher", "format"), E("format", "end")],
}

WEBHOOK = {
    "format": "flowpilot.workflow/v1", "policy": {},
    "nodes": [
        N("start", "start", 20, 220, "Order webhook", trigger="webhook",
          input_schema={"required": ["event_type", "amount"], "properties": {"event_type": {"type": "string"}, "amount": {"type": "number"}, "customer": {"type": "string"}}}),
        N("known", "condition", 260, 200, "Known event?", expression="input.event_type in ['order.created', 'order.refunded']"),
        N("ignored", "end", 520, 40, "Ignored", output={"routed": "ignored", "reason": "unsupported event type"}),
        N("big", "condition", 520, 260, "Amount ≥ 1000?", expression="input.amount >= 1000"),
        N("hi", "set_variable", 780, 180, "Priority: high", name="priority", value="high"),
        N("notify", "tool", 1020, 180, "Alert finance", tool="mock.send_email", args={"to": "finance@example.test", "subject": "High value order {{ input.amount }}", "body": "Customer {{ input.customer }}"},
          idempotency_key="{{ run.id }}-finance-alert"),
        N("lo", "set_variable", 780, 360, "Priority: normal", name="priority", value="normal"),
        N("end", "end", 1280, 260, "Routed", output={"routed": "{{ vars.priority }}", "amount": "{{ input.amount }}"}),
    ],
    "edges": [E("start", "known"), E("known", "big", "true"), E("known", "ignored", "false"), E("big", "hi", "true"), E("big", "lo", "false"),
              E("hi", "notify"), E("notify", "end"), E("lo", "end")],
}

RESILIENT = {
    "format": "flowpilot.workflow/v1", "policy": {},
    "nodes": [
        N("start", "start", 20, 180, "Customer", trigger="manual", input_schema={"required": ["customer"], "properties": {"customer": {"type": "string"}}}),
        N("policy", "retry", 250, 70, "Retry policy (brittle: 1 attempt)", max_attempts=1, backoff_ms=200, max_backoff_ms=2000, safe_to_retry=False),
        N("lookup", "tool", 250, 180, "Look up account (flaky)", tool="mock.flaky_lookup", args={"key": "{{ input.customer }}", "fail_times": 2, "error_kind": "unavailable"}),
        N("summarize", "llm", 520, 180, "Summarize account", provider="mock", model="mock-agent-1", tools=[], max_iterations=2,
          prompt="Write a one-line account summary for {{ input.customer }}: {{ nodes.lookup.value }}"),
        N("email", "tool", 790, 180, "Send summary email", tool="mock.send_email", requires_approval=True,
          args={"to": "owner@example.test", "subject": "Account summary: {{ input.customer }}", "body": "{{ nodes.summarize.text }}"},
          idempotency_key="{{ run.id }}-summary"),
        N("end", "end", 1060, 180, "Done", output={"summary": "{{ nodes.summarize.text }}", "email": "{{ nodes.email }}"}),
    ],
    "edges": [E("start", "policy"), E("policy", "lookup"), E("lookup", "summarize"), E("summarize", "email"), E("email", "end")],
}

SUPPORT = {
    "format": "flowpilot.workflow/v1", "policy": {},
    "nodes": [
        N("start", "start", 20, 160, "Question", trigger="manual", input_schema={"required": ["question"], "properties": {"question": {"type": "string"}}}),
        N("agent", "llm", 290, 120, "Support agent (RAG)", provider="mock", model="mock-agent-1", tools=["knowledge.search"], max_iterations=3,
          system="Answer only from the knowledge base and cite sources.",
          prompt='Customer question: {{ input.question }}\n[[tool:knowledge.search {"kb": "sample-handbook", "query": "{{ input.question }}", "limit": 2}]]'),
        N("end", "end", 600, 160, "Answer", output={"answer": "{{ nodes.agent.text }}"}),
    ],
    "edges": [E("start", "agent"), E("agent", "end")],
}

INCIDENT = {
    "format": "flowpilot.workflow/v1", "policy": {},
    "nodes": [
        N("start", "start", 20, 200, "Alert webhook", trigger="webhook", dedupe_key="{{ input.alert_id }}",
          input_schema={"required": ["alert_id", "severity", "service"], "properties": {"alert_id": {"type": "string"}, "severity": {"type": "string"}, "service": {"type": "string"}}}),
        N("sev", "condition", 270, 180, "Page-worthy?", expression="input.severity in ['critical', 'high']"),
        N("page", "tool", 540, 100, "Page on-call", tool="mock.send_email", idempotency_key="{{ input.alert_id }}-page",
          args={"to": "oncall@example.test", "subject": "[{{ input.severity }}] {{ input.service }} incident", "body": "Alert {{ input.alert_id }}"}),
        N("log", "set_variable", 540, 300, "Log only", name="routed", value="logged"),
        N("end", "end", 820, 200, "Routed", output={"alert": "{{ input.alert_id }}", "action": "{{ vars.routed }}"}),
        N("mark", "set_variable", 800, 80, "Mark paged", name="routed", value="paged"),
    ],
    "edges": [E("start", "sev"), E("sev", "page", "true"), E("sev", "log", "false"), E("page", "mark"), E("mark", "end"), E("log", "end")],
}

DIGEST = {
    "format": "flowpilot.workflow/v1", "policy": {},
    "nodes": [
        N("start", "start", 20, 160, "Every weekday 09:00 UTC", trigger="schedule", cron="0 9 * * 1-5", schedule_input={"topic": "bounded retries"}),
        N("find", "tool", 290, 160, "Gather sources", tool="mock.search", args={"query": "{{ input.topic }}", "limit": 3}),
        N("sum", "llm", 560, 140, "Write digest", provider="mock", model="mock-agent-1", tools=[], max_iterations=2, prompt="Write a short digest of: {{ nodes.find.results }}"),
        N("send", "tool", 830, 160, "Email digest", tool="mock.send_email", requires_approval=True, idempotency_key="{{ run.id }}-digest",
          args={"to": "team@example.test", "subject": "Daily digest: {{ input.topic }}", "body": "{{ nodes.sum.text }}"}),
        N("end", "end", 1100, 160, "Done", output={"sent": "{{ nodes.send }}"}),
    ],
    "edges": [E("start", "find"), E("find", "sum"), E("sum", "send"), E("send", "end")],
}

EXAMPLES = {
    "research": {"name": "Research & summarize (sample)", "description": "Mock research agent searches a built-in SAMPLE corpus and returns a cited report.", "definition": RESEARCH, "sample_input": {"topic": "bounded retries and idempotency"}},
    "webhook": {"name": "Webhook order router (sample)", "description": "Validates a webhook payload and routes it by conditions; high-value orders alert finance after approval.", "definition": WEBHOOK, "sample_input": {"event_type": "order.created", "amount": 2400, "customer": "acme"}},
    "resilient": {"name": "Resilient agent with failure (sample)", "description": "A flaky lookup fails twice. With 1 attempt the run fails (a regression candidate); raise retries to 3 to recover.", "definition": RESILIENT, "sample_input": {"customer": "acme"}},
    "support": {"name": "Support answers from your docs (sample)", "category": "Knowledge", "description": "RAG agent answers from the 'sample-handbook' knowledge base and cites sources.", "definition": SUPPORT, "sample_input": {"question": "How long do refunds take?"}, "setup": "support_kb"},
    "incident": {"name": "Incident alert router (sample)", "category": "Operations", "description": "Deduped alert webhook; critical/high alerts page on-call (approval-gated, idempotent), others are logged.", "definition": INCIDENT, "sample_input": {"alert_id": "a-100", "severity": "critical", "service": "payments"}},
    "digest": {"name": "Scheduled daily digest (sample)", "category": "Scheduling", "description": "Cron-triggered research digest, emailed after approval. Enable the schedule from the Studio.", "definition": DIGEST, "sample_input": {"topic": "bounded retries"}},
}
for _k, _v in EXAMPLES.items():
    _v.setdefault("category", {"research": "Research", "webhook": "Operations", "resilient": "Reliability"}.get(_k, "General"))
