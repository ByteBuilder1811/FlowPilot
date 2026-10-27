import asyncio
import os
import hmac
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.api.routes import router
from backend.db import models  # noqa: F401
from backend.db.migrate import upgrade_head
from backend.execution.worker import Worker, recover_interrupted
from backend.security import auth

log = logging.getLogger("flowpilot")
worker: Worker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    upgrade_head()
    auth.admin_token()
    n = recover_interrupted(force=os.environ.get("FLOWPILOT_SINGLE_PROCESS", "1") != "0")
    if n:
        log.warning("re-queued %d interrupted run(s) after restart", n)
    if os.environ.get("FLOWPILOT_EMBEDDED_WORKER", "1") != "0":
        worker = Worker()
        worker.start()
    yield
    if worker:
        worker.shutdown()


def _audit(method: str, path: str, status: int, actor: str = "local-admin"):
    from backend.db.models import AuditEntry
    from backend.db.session import now, session
    try:
        with session() as s:
            s.add(AuditEntry(ts=now(), actor=actor[:64], method=method, path=path[:300], status=status))
    except Exception:  # noqa: BLE001 — auditing must never break a request
        log.exception("audit write failed")


app = FastAPI(title="FlowPilot", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["X-FlowPilot-Token", "X-FlowPilot-Webhook-Token", "Content-Type", "Last-Event-ID"])


@app.middleware("http")
async def host_guard(request: Request, call_next):
    """Reject non-local Host headers (DNS-rebinding defence) unless explicitly allowed for deployment."""
    host = request.headers.get("host", "")
    hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    if hostname.lower() not in config.LOCAL_HOSTS and not request.url.path.startswith("/api/hooks/"):
        import os
        if hostname.lower() not in {h for h in os.environ.get("FLOWPILOT_ALLOWED_HOSTS", "").split(",") if h}:
            return JSONResponse({"detail": {"code": "bad_host", "message": "Host not allowed"}}, status_code=403)
    resp = await call_next(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/hooks/", "/api/v1/")):
        actor = getattr(request.state, "actor", None)  # set by the auth dependency: local-admin, service-token or the user's email
        if actor:
            await asyncio.to_thread(_audit, request.method, request.url.path, resp.status_code, actor)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store") if request.url.path.startswith("/api") else resp.headers.get("Cache-Control", "")
    return resp


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errs = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse({"detail": {"code": "validation_error", "message": "Invalid request", "errors": errs}}, status_code=422)


app.include_router(router)

dist = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = dist / path
        return FileResponse(f if f.is_file() and dist in f.resolve().parents else dist / "index.html")
