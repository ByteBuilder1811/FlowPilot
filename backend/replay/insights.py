"""Operational analytics computed from persisted live runs only (no sampling, nothing invented)."""
from collections import Counter, defaultdict
from datetime import timedelta

from backend.db.models import ApprovalRequest, Run, RunStep, ToolCall
from backend.db.session import now, session


def _pct(vals: list[float], p: float):
    if not vals:
        return None
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(round(p * (len(v) - 1))))])


def compute(days: int = 14) -> dict:
    since = now() - timedelta(days=days)
    with session() as s:
        runs = s.query(Run).filter(Run.mode == "live", Run.created_at >= since).all()
        ids = [r.id for r in runs]
        calls = s.query(ToolCall).filter(ToolCall.run_id.in_(ids or [""])).all() if ids else []
        steps = s.query(RunStep).filter(RunStep.run_id.in_(ids or [""])).all() if ids else []
        aps = s.query(ApprovalRequest).filter(ApprovalRequest.run_id.in_(ids or [""])).all() if ids else []
        by_day: dict[str, Counter] = defaultdict(Counter)
        durs = []
        errors: Counter = Counter()
        for r in runs:
            by_day[r.created_at.strftime("%Y-%m-%d")][r.status] += 1
            if r.completed_at and r.started_at and r.status in ("succeeded", "failed"):
                durs.append((r.completed_at - r.started_at).total_seconds() * 1000)
            if r.error:
                errors[r.error.get("code", "unknown")] += 1
        tools: dict[str, dict] = defaultdict(lambda: {"calls": 0, "failed": 0, "unknown": 0})
        for c in calls:
            if c.kind != "tool":
                continue
            t = tools[c.tool]
            t["calls"] += 1
            t["failed"] += c.outcome in ("error", "unknown")
            t["unknown"] += c.outcome == "unknown"
        node_d: dict[str, list[float]] = defaultdict(list)
        for st in steps:
            if st.ended_at and st.started_at:
                node_d[f"{st.node_type}:{st.node_id}"].append((st.ended_at - st.started_at).total_seconds() * 1000)
        slow = sorted(({"node": k, "avg_ms": round(sum(v) / len(v)), "samples": len(v)} for k, v in node_d.items()), key=lambda x: -x["avg_ms"])[:6]
        decided = [(a.decided_at - a.created_at).total_seconds() for a in aps if a.decided_at and a.status != "pending" and not (a.decision or {}).get("replayed")]
        tok_in = sum((r.usage or {}).get("input_tokens") or 0 for r in runs)
        tok_out = sum((r.usage or {}).get("output_tokens") or 0 for r in runs)
        reported = any((r.usage or {}).get("tokens_reported") for r in runs)
        total = len(runs)
        done = [r for r in runs if r.status in ("succeeded", "failed", "timed_out", "cancelled")]
        return {
            "days": days, "total_runs": total, "finished": len(done),
            "success_rate": round(sum(r.status == "succeeded" for r in done) / len(done), 3) if done else None,
            "duration_ms": {"p50": _pct(durs, .5), "p95": _pct(durs, .95), "samples": len(durs)},
            "retries": sum((r.usage or {}).get("retries", 0) for r in runs),
            "runs_with_retries": sum(1 for r in runs if (r.usage or {}).get("retries", 0) > 0),
            "by_day": [{"day": d, **{k: v for k, v in c.items()}} for d, c in sorted(by_day.items())],
            "errors": [{"code": k, "count": v} for k, v in errors.most_common(8)],
            "tools": sorted(({"tool": k, **v, "failure_rate": round(v["failed"] / v["calls"], 3)} for k, v in tools.items()), key=lambda x: -x["calls"]),
            "slow_nodes": slow,
            "approvals": {"decided": len(decided), "median_wait_s": _pct([x * 1000 for x in decided], .5) and round(_pct([x * 1000 for x in decided], .5) / 1000, 1), "pending": sum(a.status == "pending" for a in aps)},
            "tokens": {"input": tok_in, "output": tok_out} if reported else None, "cost_usd": None,
            "notes": ["Live runs only: fixture replays are excluded (eval runs are real executions and are included)", "Token counts only appear when a real provider reports them; cost is never estimated"],
        }
