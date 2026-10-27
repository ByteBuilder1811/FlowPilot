"""Encrypted credential vault (Fernet). Master key: FLOWPILOT_MASTER_KEY env var, else a generated 0600 file in the data dir.
Values are never returned by the API, never logged, and never placed in tool arguments."""
import os
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from backend import config
from backend.db.models import Credential, OAuthApp
from backend.db.session import now, session

_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet:
        return _fernet
    key = os.environ.get("FLOWPILOT_MASTER_KEY")
    if not key:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        f = config.DATA_DIR / ".master_key"
        if f.exists():
            key = f.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            f.write_text(key)
            os.chmod(f, 0o600)
    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def put(name: str, value: str, kind: str = "secret", meta: dict | None = None) -> None:
    ct = _f().encrypt(value.encode()).decode()
    with session() as s:
        row = s.get(Credential, name)
        if row:
            row.ciphertext, row.kind, row.meta, row.updated_at = ct, kind, meta if meta is not None else row.meta, now()
        else:
            s.add(Credential(name=name, kind=kind, ciphertext=ct, meta=meta or {}, updated_at=now()))


def delete(name: str) -> bool:
    with session() as s:
        row = s.get(Credential, name)
        if not row:
            return False
        s.delete(row)
        return True


def listing() -> list[dict]:
    with session() as s:
        return [{"name": c.name, "kind": c.kind, "meta": {k: v for k, v in (c.meta or {}).items() if k in ("app", "expires_at", "scopes")}, "updated_at": c.updated_at.isoformat()} for c in s.query(Credential).order_by(Credential.name).all()]


def _read(name: str) -> tuple[str | None, dict]:
    with session() as s:
        row = s.get(Credential, name)
        if not row:
            return None, {}
        try:
            return _f().decrypt(row.ciphertext.encode()).decode(), dict(row.meta or {})
        except InvalidToken:
            return None, {}


def get(name: str) -> str | None:
    """Vault first (refreshing OAuth tokens that are about to expire), then the environment."""
    val, meta = _read(name)
    if val is not None:
        exp = meta.get("expires_at")
        if exp and meta.get("app") and datetime.fromisoformat(exp).timestamp() - time.time() < 60:
            from backend.security import oauth
            fresh = oauth.refresh(meta["app"], name)
            if fresh:
                return fresh
        return val
    return os.environ.get(name) or None


def configured(name: str) -> bool:
    return get(name) is not None
