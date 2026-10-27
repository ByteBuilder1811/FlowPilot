"""OAuth 2.0 authorization-code + PKCE for connectors. Tokens go straight into the encrypted vault; the API never returns them."""
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from backend.db.models import OAuthApp, OAuthState
from backend.db.session import now, session
from backend.security import vault
from backend.tools.base import CallInfo, ToolError


def redirect_uri() -> str:
    return os.environ.get("FLOWPILOT_PUBLIC_URL", "http://localhost:5173").rstrip("/") + "/api/oauth/callback"


def _post_form(url: str, data: dict) -> dict:
    from backend.tools.builtin import http_request  # SSRF-guarded client
    r = http_request(CallInfo("oauth", "oauth", 0, 1, None, None, lambda: False),
                     {"url": url, "method": "POST", "headers": {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, "body": urlencode(data), "timeout_s": 20})
    j = r.get("json")
    if not isinstance(j, dict):
        raise ToolError("malformed_response", "token endpoint did not return JSON", False, None)
    return j


def start(app_name: str) -> str:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    with session() as s:
        app = s.get(OAuthApp, app_name)
        if not app:
            raise KeyError(app_name)
        s.query(OAuthState).filter(OAuthState.expires_at < now()).delete()
        s.add(OAuthState(state=state, app=app_name, verifier=verifier, expires_at=now() + timedelta(minutes=10)))
        q = {"response_type": "code", "client_id": app.client_id, "redirect_uri": redirect_uri(), "scope": app.scopes, "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"}
        return f"{app.auth_url}{'&' if '?' in app.auth_url else '?'}{urlencode(q)}"


def _store(app: OAuthApp, tok: dict, keep_refresh: str | None = None) -> None:
    access = tok.get("access_token")
    if not access:
        raise ToolError("auth", f"token endpoint returned no access_token ({tok.get('error', 'unknown error')})", False, False)
    exp = (datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)))).isoformat()
    vault.put(app.credential_name, access, kind="oauth", meta={"app": app.name, "expires_at": exp, "scopes": app.scopes})
    refresh = tok.get("refresh_token") or keep_refresh
    if refresh:
        vault.put(app.credential_name + "__REFRESH", refresh, kind="oauth", meta={"app": app.name})


def callback(state: str, code: str) -> str:
    with session() as s:
        st = s.get(OAuthState, state)
        if not st or st.expires_at < now():
            raise ValueError("unknown or expired OAuth state")
        app_name, verifier = st.app, st.verifier
        s.delete(st)  # single use
        app = s.get(OAuthApp, app_name)
        s.expunge(app)
    secret = vault.get(app.client_secret_ref)
    tok = _post_form(app.token_url, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(), "client_id": app.client_id,
                                     **({"client_secret": secret} if secret else {}), "code_verifier": verifier})
    _store(app, tok)
    return app_name


def refresh(app_name: str, credential_name: str) -> str | None:
    with session() as s:
        app = s.get(OAuthApp, app_name)
        if app:
            s.expunge(app)
    rt, _ = vault._read(credential_name + "__REFRESH")
    if not app or not rt:
        return None
    try:
        tok = _post_form(app.token_url, {"grant_type": "refresh_token", "refresh_token": rt, "client_id": app.client_id, **({"client_secret": vault.get(app.client_secret_ref)} if vault.get(app.client_secret_ref) else {})})
        _store(app, tok, keep_refresh=rt)
    except ToolError:
        return None
    return vault.get(credential_name) if not (tok.get("access_token") is None) else None
