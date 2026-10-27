import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_REVS = sorted(p.name.split("_")[0] for p in (ROOT / "backend/db/migrations/versions").glob("0*.py"))
HEAD, PREV = _REVS[-1], _REVS[-2]


def _alembic(data_dir: str, *args):
    env = {**os.environ, "FLOWPILOT_DATA_DIR": data_dir, "FLOWPILOT_DB_URL": f"sqlite:///{data_dir}/m.db"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def test_upgrade_from_populated_previous_revision_keeps_data():
    d = tempfile.mkdtemp(prefix="fp_mig_")
    assert _alembic(d, "upgrade", PREV).returncode == 0
    con = sqlite3.connect(f"{d}/m.db")
    con.execute("insert into workflows (id,name,description,draft,is_sample,deleted,created_at,updated_at) values ('wf1','a','', '{}',0,0,'2026-01-01','2026-01-01')")
    con.execute("insert into workflow_versions (id,workflow_id,version_no,definition,content_hash,note,created_at) values ('v1','wf1',1,'{}','h','','2026-01-01')")
    con.commit(); con.close()
    # the app's own upgrade path (FK-safe), not raw alembic
    code = "from backend.db.migrate import upgrade_head; upgrade_head()"
    env = {**os.environ, "FLOWPILOT_DATA_DIR": d, "FLOWPILOT_DB_URL": f"sqlite:///{d}/m.db"}
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    con = sqlite3.connect(f"{d}/m.db")
    assert con.execute("select version_num from alembic_version").fetchone()[0] == HEAD
    assert con.execute("select id, schedule_enabled from workflows").fetchall() == [("wf1", 0)]
    assert con.execute("select count(*) from workflow_versions").fetchone()[0] == 1
    assert con.execute("pragma foreign_key_check").fetchall() == []
