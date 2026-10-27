"""Minimal 5-field cron (minute hour day-of-month month day-of-week), UTC. Supports *, lists, ranges, steps."""
from datetime import datetime, timedelta, timezone

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


class CronError(ValueError):
    pass


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, st = part.split("/", 1)
            if not st.isdigit() or int(st) < 1:
                raise CronError(f"bad step '{st}'")
            step = int(st)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                raise CronError(f"bad range '{part}'")
            a, b = int(x), int(y)
        elif part.isdigit():
            a = int(part)
            b = hi if step > 1 else a
        else:
            raise CronError(f"bad value '{part}'")
        if a < lo or b > hi or a > b:
            raise CronError(f"value out of range {lo}-{hi}: '{part}'")
        out |= set(range(a, b + 1, step))
    return out


def parse(expr: str) -> list[set[int]]:
    parts = str(expr).split()
    if len(parts) != 5:
        raise CronError("cron needs 5 fields: minute hour day-of-month month day-of-week")
    sets = [_field(p, *r) for p, r in zip(parts, RANGES)]
    return sets


def matches(expr: str, when: datetime) -> bool:
    mi, h, dom, mo, dow = parse(expr)
    return when.minute in mi and when.hour in h and when.day in dom and when.month in mo and ((when.weekday() + 1) % 7) in dow


def next_fire(expr: str, after: datetime, horizon_days: int = 400) -> datetime | None:
    t = after.replace(second=0, microsecond=0, tzinfo=timezone.utc) + timedelta(minutes=1)
    for _ in range(horizon_days * 1440):
        if matches(expr, t):
            return t
        t += timedelta(minutes=1)
    return None
