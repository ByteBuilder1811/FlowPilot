from backend import config
from backend.db.models import Setting
from backend.db.session import session


def get_settings() -> dict:
    with session() as s:
        row = s.get(Setting, "policy")
        return {**config.DEFAULTS, **(row.value if row else {})}


def put_settings(values: dict) -> dict:
    cur = get_settings()
    new = {**cur, **values}
    with session() as s:
        row = s.get(Setting, "policy")
        if row:
            row.value = new
        else:
            s.add(Setting(key="policy", value=new))
    return new
