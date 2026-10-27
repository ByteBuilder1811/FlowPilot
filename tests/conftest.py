import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="fp_test_")
os.environ["FLOWPILOT_DATA_DIR"] = _tmp
os.environ["FLOWPILOT_DB_URL"] = os.environ.get("FLOWPILOT_TEST_PG_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["FLOWPILOT_API_TOKEN"] = "test-admin-token"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.db.migrate import upgrade_head  # noqa: E402
from backend.execution.worker import Worker  # noqa: E402

AUTH = {"X-FlowPilot-Token": "test-admin-token"}


@pytest.fixture(scope="session", autouse=True)
def _db():
    if os.environ.get("FLOWPILOT_TEST_PG_URL"):  # persistent server: start every session from an empty schema
        from sqlalchemy import text

        from backend.db.session import engine
        with engine().begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    upgrade_head()
    w = Worker(concurrency=3, poll=0.05)
    w.start()
    yield
    w.shutdown()


@pytest.fixture()
def client():
    from backend.main import app
    # the app lifespan would start another worker; tests drive the shared one directly
    return TestClient(app, base_url="http://localhost:8000")


@pytest.fixture()
def api(client):
    class Api:
        def get(self, *a, **k): return client.get(*a, **k)
        def post(self, *a, **k): return client.post(*a, headers={**AUTH, **k.pop("headers", {})}, **k)
        def put(self, *a, **k): return client.put(*a, headers={**AUTH, **k.pop("headers", {})}, **k)
        def delete(self, *a, **k): return client.delete(*a, headers={**AUTH, **k.pop("headers", {})}, **k)
        raw = client
    return Api()
