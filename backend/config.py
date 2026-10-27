"""Runtime configuration and hard safety limits. Env vars use the FLOWPILOT_ prefix."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FLOWPILOT_DATA_DIR", ROOT / "data"))
DB_URL = os.environ.get("FLOWPILOT_DB_URL", f"sqlite:///{DATA_DIR / 'flowpilot.db'}")

# Hard ceilings: workflow/workspace policies may lower these but never raise them.
HARD_MAX_STEPS = 1000
HARD_MAX_RUN_SECONDS = 3600
HARD_MAX_LOOP_ITERATIONS = 100
HARD_MAX_AGENT_ITERATIONS = 25
HARD_MAX_RETRIES = 5
HARD_MAX_BACKOFF_MS = 30_000
HARD_MAX_DELAY_SECONDS = 3600
MAX_PAYLOAD_BYTES = 256 * 1024  # webhook / run input
MAX_HTTP_RESPONSE_BYTES = 1024 * 1024
MAX_WORKFLOW_BYTES = 512 * 1024
MAX_NODES = 200

DEFAULTS = {
    "max_steps": 200,
    "max_run_seconds": 300,
    "max_agent_iterations": 8,
    "require_approval_for_side_effects": True,
    "loop_repeat_threshold": 3,
}

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
ALLOWED_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8000", "http://127.0.0.1:8000",
]
# Dev/test only: comma-separated hostnames the HTTP tool may reach even if private.
HTTP_ALLOW_PRIVATE_HOSTS = {h for h in os.environ.get("FLOWPILOT_HTTP_ALLOW_PRIVATE_HOSTS", "").split(",") if h}
