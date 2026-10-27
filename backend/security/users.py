"""Team mode: users, roles, server-side sessions.

Modes: 'local' (default; single trusted user, X-FlowPilot-Token) and 'users' (login required; roles enforced).
A static service token (FLOWPILOT_API_TOKEN / X-FlowPilot-Token) keeps working in BOTH modes as an automation credential, but in
'users' mode /api/session no longer hands it out, so browsers must log in.
Roles: viewer (read), editor (build/run/approve), admin (users, settings, credentials, OAuth apps)."""
import hashlib
import hmac
import os
import secrets
import time
from datetime import timedelta

from fastapi import HTTPException, Request

from backend.db.models import SessionRow, Setting, User
from backend.db.session import new_id, now, session

ROLES = {"viewer": 1, "editor": 2, "admin": 3}
COOKIE = "fp_session"
SESSION_HOURS = 12
_mode_cache: tuple[float, str] = (0.0, "local")


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


def get_mode() -> str:
    global _mode_cache
    env = os.environ.get("FLOWPILOT_AUTH_MODE")
    if env in ("local", "users"):
        return env
    t, m = _mode_cache
    if time.time() - t < 1.0:
        return m
    with session() as s:
        row = s.get(Setting, "auth_mode")
        m = (row.value or {}).get("mode", "local") if row else "local"
    _mode_cache = (time.time(), m)
    return m


def set_mode(mode: str) -> None:
    global _mode_cache
    with session() as s:
        row = s.get(Setting, "auth_mode")
        if row:
            row.value = {"mode": mode}
        else:
            s.add(Setting(key="auth_mode", value={"mode": mode}))
    _mode_cache = (time.time(), mode)


def user_count() -> int:
    with session() as s:
        return s.query(User).count()


def create_user(email: str, name: str, password: str, role: str) -> str:
    uid = new_id("usr")
    with session() as s:
        s.add(User(id=uid, email=email.lower().strip(), name=name.strip()[:120], role=role, pw_hash=hash_password(password), active=True, created_at=now()))
    return uid


def login(email: str, password: str) -> tuple[str, str, dict] | None:
    with session() as s:
        u = s.query(User).filter_by(email=email.lower().strip()).first()
        ok = bool(u and u.active and verify_password(password, u.pw_hash))
        if not ok:
            verify_password(password, "scrypt$" + "00" * 16 + "$" + "00" * 32)  # equalise timing for unknown users
            return None
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        s.query(SessionRow).filter(SessionRow.expires_at < now()).delete()
        s.add(SessionRow(id=hashlib.sha256(token.encode()).hexdigest(), user_id=u.id, csrf=csrf, created_at=now(), expires_at=now() + timedelta(hours=SESSION_HOURS)))
        return token, csrf, {"id": u.id, "email": u.email, "name": u.name, "role": u.role}


def logout(token: str) -> None:
    with session() as s:
        s.query(SessionRow).filter_by(id=hashlib.sha256(token.encode()).hexdigest()).delete()


def resolve(request: Request) -> dict | None:
    """The logged-in user for this request (cookie session), or None."""
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    with session() as s:
        row = s.get(SessionRow, hashlib.sha256(tok.encode()).hexdigest())
        if not row or row.expires_at < now():
            return None
        u = s.get(User, row.user_id)
        if not u or not u.active:
            return None
        return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "csrf": row.csrf}


def _service_token_ok(request: Request) -> bool:
    from backend.security import auth
    t = request.headers.get("x-flowpilot-token")
    return bool(t and hmac.compare_digest(t, auth.admin_token()))


def authorize(request: Request, min_role: str, write: bool) -> str:
    """Return the actor name or raise 401/403."""
    if _service_token_ok(request):
        return "service-token" if get_mode() == "users" else "local-admin"
    if get_mode() == "users":
        u = resolve(request)
        if not u:
            raise HTTPException(401, {"code": "login_required", "message": "Please sign in"})
        if write and not hmac.compare_digest(request.headers.get("x-flowpilot-csrf", ""), u["csrf"]):
            raise HTTPException(403, {"code": "csrf", "message": "Missing or invalid CSRF token"})
        if ROLES[u["role"]] < ROLES[min_role]:
            raise HTTPException(403, {"code": "forbidden", "message": f"This action needs the '{min_role}' role (you are '{u['role']}')"})
        request.state.user = u
        return u["email"]
    raise HTTPException(401, {"code": "unauthorized", "message": "Missing or invalid X-FlowPilot-Token"})
