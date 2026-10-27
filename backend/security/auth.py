"""Local-workspace authorization.

Single-user local mode: a random admin token is generated on first start (or taken from
FLOWPILOT_API_TOKEN). Mutating endpoints require it in `X-FlowPilot-Token`. The SPA obtains it
from /api/session, which only answers same-machine requests with a localhost Host/Origin.
This is CSRF/drive-by protection for a local app, NOT multi-user authentication.
"""
import hmac
import os
import secrets
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

from backend import config

_token: str | None = None


def admin_token() -> str:
    global _token
    if _token:
        return _token
    env = os.environ.get("FLOWPILOT_API_TOKEN")
    if env:
        _token = env
    else:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        f = config.DATA_DIR / ".admin_token"
        if f.exists():
            _token = f.read_text().strip()
        else:
            _token = secrets.token_urlsafe(32)
            f.write_text(_token)
            os.chmod(f, 0o600)
    return _token


def host_is_local(request: Request) -> bool:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") else "[::1]"
    return host.lower() in config.LOCAL_HOSTS


def origin_is_local(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return (urlsplit(origin).hostname or "") in {"localhost", "127.0.0.1", "::1"}


def require_admin(x_flowpilot_token: str | None = Header(default=None)) -> None:
    if not x_flowpilot_token or not hmac.compare_digest(x_flowpilot_token, admin_token()):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing or invalid X-FlowPilot-Token"})
