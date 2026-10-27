import json
import time

import httpx

from backend import config
from backend.db.models import MockOutbox
from backend.db.session import now, session
from backend.security import ssrf
from backend.tools.base import CallInfo, ToolError, ToolSpec

_SAMPLE_CORPUS = [
    ("Durable execution basics", "https://example.org/sample/durable-execution", "Durable execution persists each step's result so a crashed run can resume without repeating completed work."),
    ("Idempotency keys", "https://example.org/sample/idempotency-keys", "An idempotency key lets a server recognise a retried request and return the original result instead of repeating the side effect."),
    ("Bounded retries", "https://example.org/sample/bounded-retries", "Retries should be limited, use exponential backoff with a cap, and only apply to errors that are plausibly transient."),
    ("Human-in-the-loop approvals", "https://example.org/sample/approvals", "Sensitive operations should pause for an explicit human decision that is bound to the exact proposed arguments."),
]


def mock_search(info: CallInfo, args: dict):
    q = str(args.get("query", "")).lower()
    words = {w for w in q.replace("?", " ").split() if len(w) > 3}
    scored = sorted(_SAMPLE_CORPUS, key=lambda d: -sum(w in (d[0] + d[2]).lower() for w in words))
    return {"sample_data": True, "query": args.get("query"), "results": [{"title": t, "url": u, "snippet": s} for t, u, s in scored[: int(args.get("limit", 3))]]}


def mock_flaky(info: CallInfo, args: dict):
    fail_times = int(args.get("fail_times", 2))
    if info.ordinal < fail_times:
        kind = args.get("error_kind", "unavailable")
        raise ToolError(kind, f"simulated {kind} from mock.flaky_lookup (call {info.ordinal + 1} of {fail_times} planned failures)", transient=True, executed=False)
    return {"key": args.get("key"), "value": f"value-for-{args.get('key')}", "served_after_failures": fail_times}


def _outbox_add(info: CallInfo, tool: str, payload: dict) -> int:
    with session() as s:
        row = MockOutbox(run_id=info.run_id, tool=tool, idempotency_key=info.idempotency_key, approval_id=info.approval_id, payload=payload, created_at=now())
        s.add(row)
        s.flush()
        return row.id


def _outbox_find(key: str | None, tool: str):
    if not key:
        return None
    with session() as s:
        return s.query(MockOutbox).filter_by(idempotency_key=key, tool=tool).first()


def mock_send_email(info: CallInfo, args: dict):
    existing = _outbox_find(info.idempotency_key, "mock.send_email")
    if existing:  # server-side dedupe: same key → original result, no second email
        return {"message_id": f"msg_{existing.id}", "deduplicated": True}
    mid = _outbox_add(info, "mock.send_email", {"to": args.get("to"), "subject": args.get("subject"), "body": args.get("body")})
    if args.get("simulate") == "unknown_outcome" and info.attempt == 1:
        raise ToolError("timeout", "connection timed out after the request was sent (outcome unknown)", transient=True, executed=None)
    return {"message_id": f"msg_{mid}", "deduplicated": False}


def mock_charge(info: CallInfo, args: dict):
    mid = _outbox_add(info, "mock.charge_card", {"amount": args.get("amount"), "customer": args.get("customer")})
    if args.get("simulate") == "unknown_outcome" and info.attempt == 1:
        raise ToolError("timeout", "gateway timeout after submitting charge (outcome unknown)", transient=True, executed=None)
    return {"charge_id": f"ch_{mid}", "amount": args.get("amount")}


def http_request(info: CallInfo, args: dict):
    method = str(args.get("method", "GET")).upper()
    url = str(args.get("url", ""))
    try:
        scheme, host, port, ip = ssrf.validate_url(url)
    except ssrf.SSRFBlocked as e:
        raise ToolError("blocked", f"Blocked by SSRF protection: {e}", transient=False, executed=False) from e
    headers = {str(k): str(v) for k, v in (args.get("headers") or {}).items()}
    timeout = min(float(args.get("timeout_s", 15)), 60)
    body = args.get("body")
    if info.idempotency_key:
        headers.setdefault("Idempotency-Key", info.idempotency_key)
    hops = 0
    while True:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        netloc_ip = f"[{ip}]" if ":" in ip else ip
        target = urlunsplit((scheme, f"{netloc_ip}:{port}", parts.path or "/", parts.query, ""))
        h = {**headers, "Host": host if port in (80, 443) else f"{host}:{port}"}
        try:
            with httpx.Client(timeout=timeout, verify=True, follow_redirects=False) as c:
                req = c.build_request(method, target, headers=h, json=body if isinstance(body, (dict, list)) else None,
                                      content=body if isinstance(body, str) else None, extensions={"sni_hostname": host})
                resp = c.send(req, stream=True)
                try:
                    chunks, total = [], 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > config.MAX_HTTP_RESPONSE_BYTES:
                            raise ToolError("malformed_response", "response exceeded size limit", transient=False, executed=None)
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    status, rheaders = resp.status_code, dict(resp.headers)
                finally:
                    resp.close()
        except httpx.TimeoutException as e:
            raise ToolError("timeout", f"request timed out: {type(e).__name__}", transient=True, executed=None if method not in ("GET", "HEAD") else False) from e
        except httpx.HTTPError as e:
            raise ToolError("unavailable", f"connection error: {type(e).__name__}", transient=True, executed=False) from e
        if status in (301, 302, 303, 307, 308) and rheaders.get("location"):
            hops += 1
            if hops > 3:
                raise ToolError("blocked", "too many redirects", executed=None)
            from urllib.parse import urljoin
            url = urljoin(url, rheaders["location"])
            try:
                scheme, host, port, ip = ssrf.validate_url(url)
            except ssrf.SSRFBlocked as e:
                raise ToolError("blocked", f"Redirect blocked by SSRF protection: {e}", executed=None) from e
            if status in (301, 302, 303):
                method, body = "GET", None
            continue
        break
    text = raw.decode("utf-8", errors="replace")
    parsed = None
    if "json" in rheaders.get("content-type", ""):
        try:
            parsed = json.loads(text)
        except ValueError:
            raise ToolError("malformed_response", "response declared JSON but could not be parsed", transient=False, executed=None) from None
    if status == 429:
        raise ToolError("rate_limit", "HTTP 429 Too Many Requests", transient=True, executed=False)
    if status >= 500:
        raise ToolError("unavailable", f"HTTP {status} from upstream", transient=True, executed=None if method not in ("GET", "HEAD") else False)
    if status >= 400:
        raise ToolError("bad_request", f"HTTP {status}", transient=False, executed=False)
    return {"status": status, "headers": {k: v for k, v in rheaders.items() if k.lower() in ("content-type", "content-length", "etag", "last-modified")},
            "json": parsed, "text": text[:20000] if parsed is None else None}


def _s(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


REGISTRY: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("mock.search", "Search a small built-in SAMPLE corpus (deterministic, no network).", mock_search, replay_safe=True,
             schema=_s({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"])),
    ToolSpec("mock.flaky_lookup", "Lookup that fails transiently for the first N calls, then succeeds (for recovery demos).", mock_flaky, replay_safe=True,
             schema=_s({"key": {"type": "string"}, "fail_times": {"type": "integer"}, "error_kind": {"type": "string"}}, ["key"])),
    ToolSpec("mock.send_email", "Send an email via the MOCK outbox (side effect; supports idempotency keys).", mock_send_email, side_effect=True,
             supports_idempotency_key=True, schema=_s({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "simulate": {"type": "string"}}, ["to", "subject"])),
    ToolSpec("mock.charge_card", "Charge a card via the MOCK payment gateway (side effect; NO idempotency support).", mock_charge, side_effect=True,
             schema=_s({"amount": {"type": "number"}, "customer": {"type": "string"}, "simulate": {"type": "string"}}, ["amount", "customer"])),
    ToolSpec("http.request", "HTTP request to a public host (SSRF-protected). Non-GET methods are side effects.", http_request, mock=False,
             supports_idempotency_key=True, schema=_s({"url": {"type": "string"}, "method": {"type": "string"}, "headers": {"type": "object"}, "body": {}, "timeout_s": {"type": "number"}}, ["url"])),
]}


from backend.tools import knowledge as _kb  # noqa: E402

REGISTRY["knowledge.search"] = ToolSpec("knowledge.search", "Search a local knowledge base (BM25). Returns snippets with knowledge:// source ids.", _kb.tool_search, mock=False,
                                        schema=_s({"kb": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}, ["kb", "query"]))

from backend.tools.connectors import CONNECTORS  # noqa: E402

for _c in CONNECTORS:
    REGISTRY[_c.name] = _c
