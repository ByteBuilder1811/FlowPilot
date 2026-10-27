from pathlib import Path

from alembic import command
from alembic.config import Config

from backend.db.session import engine


def upgrade_head():
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    eng = engine()
    sqlite = eng.dialect.name == "sqlite"
    with eng.connect() as conn:
        if sqlite:  # batch table-recreation needs FK enforcement off (re-enabled per connection by session.py)
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
        with conn.begin():
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        if sqlite:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()
