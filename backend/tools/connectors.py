"""Declarative connectors. Credentials come from environment variables (never stored, never in tool arguments, so
never in fixtures/events). Every connector is a side effect and therefore goes through the approval gate."""
import os
import re
import smtplib
from email.message import EmailMessage

from backend.tools.base import CallInfo, ToolError, ToolSpec


def _s(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def http_request(info, args):  # late import: builtin registers these connectors at import time
    from backend.tools.builtin import http_request as _h
    return _h(info, args)


def _need(name: str, hint: str) -> str:
    from backend.security import vault
    v = vault.get(name)
    if not v:
        raise ToolError("not_configured", f"Not configured: add {name} in Integrations → Credentials, or set it as an environment variable ({hint}).", transient=False, executed=False)
    return v


def _post(info: CallInfo, url: str, body: dict, headers: dict | None = None):
    r = http_request(info, {"url": url, "method": "POST", "headers": headers or {}, "body": body, "timeout_s": 15})
    return {"status": r["status"], "ok": True}


def slack_post(info: CallInfo, args: dict):
    return _post(info, _need("SLACK_WEBHOOK_URL", "a Slack incoming-webhook URL"), {"text": str(args["text"])[:3000]})


def discord_post(info: CallInfo, args: dict):
    return _post(info, _need("DISCORD_WEBHOOK_URL", "a Discord webhook URL"), {"content": str(args["text"])[:1900]})


def telegram_send(info: CallInfo, args: dict):
    tok = _need("TELEGRAM_BOT_TOKEN", "from @BotFather")
    if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{20,}", tok):
        raise ToolError("not_configured", "TELEGRAM_BOT_TOKEN has an unexpected format", transient=False, executed=False)
    r = _post(info, f"https://api.telegram.org/bot{tok}/sendMessage", {"chat_id": str(args["chat_id"]), "text": str(args["text"])[:4000]})
    return r


def github_issue(info: CallInfo, args: dict):
    tok = _need("GITHUB_TOKEN", "a token with issues:write")
    repo = str(args["repo"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo):
        raise ToolError("bad_request", "repo must look like owner/name", transient=False, executed=False)
    r = http_request(info, {"url": f"https://api.github.com/repos/{repo}/issues", "method": "POST", "timeout_s": 15,
                            "headers": {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "FlowPilot"},
                            "body": {"title": str(args["title"])[:250], "body": str(args.get("body", ""))[:60000]}})
    j = r.get("json") or {}
    return {"status": r["status"], "number": j.get("number"), "url": j.get("html_url")}


def email_send(info: CallInfo, args: dict):
    host = _need("SMTP_HOST", "SMTP server")
    sender = _need("SMTP_FROM", "sender address")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, str(args["to"]), str(args["subject"])[:200]
    msg.set_content(str(args.get("body", "")))
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as smtp:
            if os.environ.get("SMTP_STARTTLS", "1") != "0":
                smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
        raise ToolError("bad_request", f"SMTP rejected the message: {type(e).__name__}", transient=False, executed=False) from e
    except (smtplib.SMTPException, OSError, TimeoutError) as e:  # may have been accepted before the connection dropped
        raise ToolError("timeout", f"SMTP connection problem: {type(e).__name__}", transient=True, executed=None) from e
    return {"sent": True, "to": args["to"]}


CONNECTORS = [
    ToolSpec("slack.post_message", "Post a message via a Slack incoming webhook.", slack_post, side_effect=True, mock=False, credentials=("SLACK_WEBHOOK_URL",), schema=_s({"text": {"type": "string"}}, ["text"])),
    ToolSpec("discord.post_message", "Post a message via a Discord webhook.", discord_post, side_effect=True, mock=False, credentials=("DISCORD_WEBHOOK_URL",), schema=_s({"text": {"type": "string"}}, ["text"])),
    ToolSpec("telegram.send_message", "Send a Telegram message from your bot.", telegram_send, side_effect=True, mock=False, credentials=("TELEGRAM_BOT_TOKEN",), schema=_s({"chat_id": {"type": "string"}, "text": {"type": "string"}}, ["chat_id", "text"])),
    ToolSpec("github.create_issue", "Create a GitHub issue.", github_issue, side_effect=True, mock=False, credentials=("GITHUB_TOKEN",), schema=_s({"repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}, ["repo", "title"])),
    ToolSpec("email.send", "Send an email via SMTP.", email_send, side_effect=True, mock=False, credentials=("SMTP_HOST", "SMTP_FROM"), schema=_s({"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject"])),
]


# ----------------------------------------------------------------------------- more connectors
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json as _json  # noqa: E402
import sqlite3  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import quote, urlencode  # noqa: E402

from backend import config  # noqa: E402


def _base(env: str, default: str) -> str:
    return os.environ.get(env, default).rstrip("/")


def notion_page(info: CallInfo, args: dict):
    tok = _need("NOTION_TOKEN", "an internal integration token")
    body = {"parent": {"database_id": str(args["database_id"])}, "properties": {"Name": {"title": [{"text": {"content": str(args["title"])[:200]}}]}}}
    if args.get("content"):
        body["children"] = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": str(args["content"])[:1900]}}]}}]
    r = http_request(info, {"url": f"{_base('NOTION_BASE_URL', 'https://api.notion.com')}/v1/pages", "method": "POST", "timeout_s": 15, "body": body,
                            "headers": {"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28"}})
    j = r.get("json") or {}
    return {"status": r["status"], "page_id": j.get("id"), "url": j.get("url")}


def airtable_record(info: CallInfo, args: dict):
    tok = _need("AIRTABLE_TOKEN", "a personal access token")
    base, table = str(args["base_id"]), str(args["table"])
    if not re.fullmatch(r"app[A-Za-z0-9]{6,}", base):
        raise ToolError("bad_request", "base_id looks like appXXXXXXXX", False, False)
    r = http_request(info, {"url": f"{_base('AIRTABLE_BASE_URL', 'https://api.airtable.com')}/v0/{base}/{quote(table)}", "method": "POST", "timeout_s": 15,
                            "headers": {"Authorization": f"Bearer {tok}"}, "body": {"fields": args["fields"]}})
    return {"status": r["status"], "record_id": (r.get("json") or {}).get("id")}


def stripe_payment_intent(info: CallInfo, args: dict):
    key = _need("STRIPE_API_KEY", "use a restricted or test-mode key")
    data = {"amount": str(int(args["amount_cents"])), "currency": str(args.get("currency", "usd")), "description": str(args.get("description", ""))[:200]}
    r = http_request(info, {"url": f"{_base('STRIPE_BASE_URL', 'https://api.stripe.com')}/v1/payment_intents", "method": "POST", "timeout_s": 20, "body": urlencode(data),
                            "headers": {"Authorization": f"Bearer {key}", "Content-Type": "application/x-www-form-urlencoded"}})  # Idempotency-Key is added by http_request when the node sets one
    j = r.get("json") or {}
    return {"status": r["status"], "payment_intent": j.get("id"), "state": j.get("status")}


def sheets_append(info: CallInfo, args: dict):
    tok = _need("GOOGLE_SHEETS_TOKEN", "an OAuth access token with the spreadsheets scope; connect it in Integrations → Credentials")
    sid = str(args["spreadsheet_id"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", sid):
        raise ToolError("bad_request", "spreadsheet_id has an unexpected format", False, False)
    rng = quote(str(args.get("range", "Sheet1!A1")), safe="!:")
    r = http_request(info, {"url": f"{_base('SHEETS_BASE_URL', 'https://sheets.googleapis.com')}/v4/spreadsheets/{sid}/values/{rng}:append?valueInputOption=USER_ENTERED", "method": "POST", "timeout_s": 15,
                            "headers": {"Authorization": f"Bearer {tok}"}, "body": {"values": [list(args["values"])]}})
    return {"status": r["status"], "updated": ((r.get("json") or {}).get("updates") or {}).get("updatedRows")}


def sigv4_headers(method: str, host: str, path: str, query: str, headers: dict, payload: bytes, region: str, service: str, access: str, secret: str, when: datetime) -> dict:
    """AWS Signature V4 (verified against AWS's published example in tests)."""
    amz = when.strftime("%Y%m%dT%H%M%SZ")
    day = when.strftime("%Y%m%d")
    phash = hashlib.sha256(payload).hexdigest()
    h = {**{k.lower(): str(v).strip() for k, v in headers.items()}, "host": host, "x-amz-date": amz, "x-amz-content-sha256": phash}
    signed = ";".join(sorted(h))
    canon = "\n".join([method, quote(path, safe="/~"), query, "".join(f"{k}:{h[k]}\n" for k in sorted(h)), signed, phash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (day, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return h


def s3_put(info: CallInfo, args: dict):
    access, secret = _need("AWS_ACCESS_KEY_ID", "IAM access key"), _need("AWS_SECRET_ACCESS_KEY", "IAM secret key")
    bucket, key, region = str(args["bucket"]), str(args["key"]).lstrip("/"), str(args.get("region") or os.environ.get("AWS_REGION", "us-east-1"))
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", bucket):
        raise ToolError("bad_request", "invalid bucket name", False, False)
    body = str(args["content"]).encode()
    endpoint = os.environ.get("S3_ENDPOINT")  # e.g. MinIO/R2; path-style when set
    host = (endpoint.split("://", 1)[-1].rstrip("/") if endpoint else f"{bucket}.s3.{region}.amazonaws.com")
    path = f"/{bucket}/{key}" if endpoint else f"/{key}"
    hdrs = sigv4_headers("PUT", host, path, "", {"content-type": str(args.get("content_type", "text/plain"))}, body, region, "s3", access, secret, datetime.now(timezone.utc))
    scheme = endpoint.split("://", 1)[0] if endpoint else "https"
    r = http_request(info, {"url": f"{scheme}://{host}{path}", "method": "PUT", "timeout_s": 30, "body": body.decode(), "headers": {k: v for k, v in hdrs.items() if k != "host"}})
    return {"status": r["status"], "bucket": bucket, "key": key}


def _sql_guard(stmt: str) -> str:
    s = stmt.strip().rstrip(";")
    if ";" in s or not re.match(r"(?is)^\s*(select|with)\b", s):
        raise ToolError("forbidden", "only a single SELECT/WITH statement is allowed", False, False)
    if re.search(r"(?is)\b(insert|update|delete|drop|alter|create|attach|pragma|replace|truncate|grant|copy)\b", s):
        raise ToolError("forbidden", "statement contains a write/DDL keyword; queries are read-only", False, False)
    return s


def sqlite_query(info: CallInfo, args: dict):
    base = Path(os.environ.get("FLOWPILOT_SQL_DIR", config.DATA_DIR / "files")).resolve()
    f = (base / str(args["database"])).resolve()
    if base not in f.parents or not f.is_file():
        raise ToolError("forbidden", f"database must be an existing file inside {base}", False, False)
    sql = _sql_guard(str(args["sql"]))
    con = sqlite3.connect(f"file:{f}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        deadline = datetime.now().timestamp() + 5
        con.set_progress_handler(lambda: 1 if datetime.now().timestamp() > deadline else 0, 10_000)
        cur = con.execute(sql, list(args.get("params") or []))
        cols = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(200)]
    except sqlite3.Error as e:
        raise ToolError("bad_request", f"query failed: {e}", False, False) from e
    finally:
        con.close()
    return {"columns": cols, "rows": rows, "truncated": len(rows) == 200}


def postgres_query(info: CallInfo, args: dict):
    dsn = _need("POSTGRES_DSN", "postgresql://user:pass@host/db — use a read-only role")
    sql = _sql_guard(str(args["sql"]))
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=8, options="-c default_transaction_read_only=on -c statement_timeout=8000") as con:
            cur = con.execute(sql, list(args.get("params") or []))
            cols = [d.name for d in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(200)]
    except ImportError as e:
        raise ToolError("not_configured", "psycopg is not installed", False, False) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("unavailable", f"postgres error: {type(e).__name__}", True, False) from e
    return {"columns": cols, "rows": rows, "truncated": len(rows) == 200}


CONNECTORS += [
    ToolSpec("notion.create_page", "Create a page in a Notion database.", notion_page, side_effect=True, mock=False, credentials=("NOTION_TOKEN",), schema=_s({"database_id": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}}, ["database_id", "title"])),
    ToolSpec("airtable.create_record", "Create an Airtable record.", airtable_record, side_effect=True, mock=False, credentials=("AIRTABLE_TOKEN",), schema=_s({"base_id": {"type": "string"}, "table": {"type": "string"}, "fields": {"type": "object"}}, ["base_id", "table", "fields"])),
    ToolSpec("stripe.create_payment_intent", "Create a Stripe PaymentIntent (supports idempotency keys).", stripe_payment_intent, side_effect=True, supports_idempotency_key=True, mock=False, credentials=("STRIPE_API_KEY",), schema=_s({"amount_cents": {"type": "integer"}, "currency": {"type": "string"}, "description": {"type": "string"}}, ["amount_cents"])),
    ToolSpec("sheets.append_row", "Append a row to a Google Sheet (OAuth).", sheets_append, side_effect=True, mock=False, credentials=("GOOGLE_SHEETS_TOKEN",), schema=_s({"spreadsheet_id": {"type": "string"}, "range": {"type": "string"}, "values": {"type": "array"}}, ["spreadsheet_id", "values"])),
    ToolSpec("s3.put_object", "Upload text to S3 or an S3-compatible store (SigV4).", s3_put, side_effect=True, mock=False, credentials=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"), schema=_s({"bucket": {"type": "string"}, "key": {"type": "string"}, "content": {"type": "string"}, "region": {"type": "string"}, "content_type": {"type": "string"}}, ["bucket", "key", "content"])),
    ToolSpec("sqlite.query", "Read-only SELECT against a SQLite file inside the FlowPilot files directory.", sqlite_query, mock=False, schema=_s({"database": {"type": "string"}, "sql": {"type": "string"}, "params": {"type": "array"}}, ["database", "sql"])),
    ToolSpec("postgres.query", "Read-only SELECT against Postgres (POSTGRES_DSN).", postgres_query, mock=False, credentials=("POSTGRES_DSN",), schema=_s({"sql": {"type": "string"}, "params": {"type": "array"}}, ["sql"])),
]
