"""Observability (spec section 9): usage adapters, cost, run metrics.

Token usage comes from wherever the runtime actually provides it: the
manual `tokens` command, or the `<usage>` block of a subagent completion
notification parsed by :func:`parse_notification`. Fields the source does
not provide stay None; the engine never invents numbers. Cost is computed
from a small price table and always carries a quality flag: actual,
estimated or unknown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .storage import Store, now_iso

# USD per 1M tokens: (input, output, cache_read). Quality "estimated":
# prices change, the table is a fallback, not a contract.
PRICE_TABLE_USD_1M: dict[str, tuple[float, float, float]] = {
    "glm-5.3": (0.60, 2.20, 0.10),
    "glm-5.3-flash": (0.10, 0.40, 0.02),
    "glm-5.4": (0.60, 2.20, 0.10),
    "gpt-5.2": (1.25, 10.00, 0.125),
    "claude-opus-4.6": (5.00, 25.00, 0.50),
    "claude-sonnet-4.6": (3.00, 15.00, 0.30),
    "deepseek-v4": (0.27, 1.10, 0.07),
}

_USAGE_RE = re.compile(
    r"<usage>(.*?)</usage>", re.DOTALL)
# Both telemetry formats appear in the wild: "subagent_tokens: 123" and
# "<subagent_tokens>123</subagent_tokens>".
_FIELD_RES = {
    "total_tokens": re.compile(r"subagent_tokens\s*[>:]\s*(\d+)"),
    "tool_uses": re.compile(r"tool_uses\s*[>:]\s*(\d+)"),
    "duration_ms": re.compile(r"duration_ms\s*[>:]\s*(\d+)"),
}


def parse_notification(text: str) -> dict | None:
    """Extract one usage record from a subagent notification text.

    Returns None when the text carries no `<usage>` block: an honest miss,
    not a zero.
    """
    match = _USAGE_RE.search(text)
    if not match:
        return None
    block = match.group(1)
    record: dict = {"source": "notification"}
    for key, regex in _FIELD_RES.items():
        found = regex.search(block)
        record[key] = int(found.group(1)) if found else None
    if record["total_tokens"] is None:
        return None
    return record


def record_usage(store: Store, team: str, run_id: str, task_id: str,
                 agent_id: str, record: dict) -> int | None:
    """Persist one normalized usage record (idempotent via dedup_key)."""
    entry = {
        "team_id": team, "run_id": run_id, "task_id": task_id,
        "agent_id": agent_id, "model": record.get("model"),
        "provider": record.get("provider"),
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "cache_read_tokens": record.get("cache_read_tokens"),
        "cache_write_tokens": record.get("cache_write_tokens"),
        "total_tokens": record.get("total_tokens"),
        "tool_uses": record.get("tool_uses"),
        "duration_ms": record.get("duration_ms"),
        "source": record.get("source", "manual"),
        "dedup_key": record.get("dedup_key"),
        "recorded_at": now_iso(),
        "raw": {k: v for k, v in record.items() if k.startswith("_")},
    }
    return store.add_usage(entry)


def compute_cost(record: dict) -> tuple[float | None, str]:
    """Cost in USD and its quality flag for one usage record."""
    model = (record.get("model") or "").lower()
    total = record.get("total_tokens")
    has_split = record.get("input_tokens") is not None \
        and record.get("output_tokens") is not None
    if not model or (total in (None, 0) and not has_split):
        return None, "unknown"
    price = None
    for name, rates in PRICE_TABLE_USD_1M.items():
        if name in model:
            price = rates
            break
    if price is None:
        return None, "unknown"
    inp = price[0]
    if record.get("input_tokens") is not None \
            and record.get("output_tokens") is not None:
        cost = (record["input_tokens"] * price[0]
                + record["output_tokens"] * price[1]
                + (record.get("cache_read_tokens") or 0) * price[2]) / 1_000_000
        quality = "estimated"
    else:
        # Split unknown: assume 1:3 input/output as an explicit estimate.
        cost = (total * 0.25 * price[0] + total * 0.75 * price[1]) / 1_000_000
        quality = "estimated"
    return round(cost, 6), quality


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def collect_metrics(store: Store, team: str, run_id: str | None = None) -> dict:
    """Run metrics (spec 9.3), multi-agent vs single-agent comparison."""
    tasks = store.list_tasks(team)
    attempts_by_task = {t["id"]: store.list_attempts(team, t["id"])
                        for t in tasks}
    usage = store.list_usage(team, run_id=run_id)

    completed = [t for t in tasks if t["status"] == "completed"]
    durations, rework = [], []
    for t in tasks:
        start = _parse(t["claimed_at"]) or _parse(t["created_at"])
        end = _parse(t["completed_at"]) or _parse(t["last_event_ts"])
        if start and end and end >= start:
            durations.append((end - start).total_seconds())
        rework.append(len(attempts_by_task[t["id"]]) - 1
                      if attempts_by_task[t["id"]] else 0)
    all_attempts = [a for attempts in attempts_by_task.values() for a in attempts]
    passes = [a for a in all_attempts if a["verdict"] == "pass"]

    tokens_by_task: dict[str, int] = {}
    cost_total = 0.0
    cost_quality = "unknown"
    for record in usage:
        tokens_by_task[record["task_id"] or ""] = \
            tokens_by_task.get(record["task_id"] or "", 0) \
            + (record.get("total_tokens") or 0)
        cost, quality = compute_cost(record)
        if cost is not None:
            cost_total += cost
            cost_quality = "estimated"

    wall = None
    if durations:
        first_start = min(
            filter(None, (_parse(t["claimed_at"]) for t in tasks)),
            default=None)
        last_end = max(
            filter(None, (_parse(t["completed_at"] or t["last_event_ts"])
                          for t in tasks)), default=None)
        if first_start and last_end:
            wall = (last_end - first_start).total_seconds()

    def avg(values):
        return round(sum(values) / len(values), 3) if values else None

    # Hardening spec 6.1 additions: execution, verification, recovery and
    # integration counters straight from the recorded history.
    submissions_all = []
    for t in tasks:
        submissions_all.extend(store.list_submissions(team, t["id"]))
    attempts_counts = [len(store.list_attempts(team, t["id"]))
                       for t in tasks]
    first_submit_lags, fix_times = [], []
    for t in tasks:
        task_subs = store.list_submissions(team, t["id"])
        if task_subs:
            started = _parse(t["claimed_at"]) or _parse(t["created_at"])
            first = _parse(task_subs[0]["created_at"])
            if started and first:
                first_submit_lags.append(
                    (first - started).total_seconds())
        fails = [a for a in store.list_attempts(team, t["id"])
                 if a["verdict"] == "fail"]
        for i, fail in enumerate(fails):
            later = [a for a in store.list_attempts(team, t["id"])
                     if a["created_at"] > fail["created_at"]]
            if later:
                fix_times.append(
                    (_parse(later[0]["created_at"])
                     - _parse(fail["created_at"])).total_seconds())
    first_pass = sum(1 for t in tasks if t["verification_failures"] == 0
                     and t["status"] == "completed")
    required_criteria = sum(
        1 for t in tasks for c in t.get("acceptance", [])
        if not isinstance(c, dict) or c.get("required", True))
    acceptance_violations = sum(
        len(t.get("metadata", {}).get("workspace_violations", []))
        for t in tasks)
    overrides = sum(1 for sub in submissions_all
                    if sub.get("verification_status") == "overridden")
    agent_timeouts = eventlog_count(store, team, "agent_timeout")
    recovered = sum(1 for t in tasks if t["restarts"] > 0)
    blocked_by_limit = eventlog_count(store, team, "task_blocked")
    quarantined = sum(1 for t in tasks
                      if t.get("metadata", {}).get("quarantine_dir"))
    conflicts = eventlog_count(store, team, "workspace_conflict")
    integrated_events = eventlog_count(store, team, "workspace_integrated")
    applied = 1 if run_status(store, team) == "finished" else 0

    base = {
        "tasks_total": len(tasks),
        "task_success_rate": round(len(completed) / len(tasks), 3)
        if tasks else None,
        "verification_attempts": len(all_attempts),
        "verification_pass_rate": round(len(passes) / len(all_attempts), 3)
        if all_attempts else None,
        "verification_fail_rate": round(
            1 - len(passes) / len(all_attempts), 3) if all_attempts else None,
        "average_rework_rounds": avg(rework),
        "average_task_duration_s": avg(durations),
        "agent_restarts": sum(t["restarts"] for t in tasks),
        "recovery_rate": round(
            sum(1 for t in tasks if t["restarts"] > 0
                and t["status"] == "completed")
            / max(1, sum(1 for t in tasks if t["restarts"] > 0)), 3)
        if any(t["restarts"] > 0 for t in tasks) else None,
        "tokens_total": sum(tokens_by_task.values()),
        "tokens_per_task": avg(list(tokens_by_task.values())),
        "cost_total_usd": round(cost_total, 6) if cost_total else 0.0,
        "cost_quality": cost_quality,
        "cost_per_task": round(cost_total / len(tasks), 6)
        if tasks and cost_total else None,
        "cost_per_successful_task": round(cost_total / len(completed), 6)
        if completed and cost_total else None,
        "parallel_execution_time_s": round(wall, 3) if wall is not None else None,
        "sequential_execution_estimate_s": round(sum(durations), 3)
        if durations else None,
        "speedup_estimate": round(sum(durations) / wall, 3)
        if wall and durations and wall > 0 else None,
        # Source quality (hardening spec 6.2): durations come from recorded
        # timestamps of real attempts, the comparison is arithmetic on
        # them; simulated numbers are never presented as measurements.
        "speedup_quality": "measured"
        if wall is not None and durations else "unknown",
        "execution_attempts": sum(t["attempt"] for t in tasks),
        "submissions": len(submissions_all),
        "avg_time_to_first_submit_s": avg(first_submit_lags),
        "avg_fix_time_after_fail_s": avg(fix_times),
        "verification_first_pass_rate": round(first_pass / len(tasks), 3)
        if tasks else None,
        "required_criteria": required_criteria,
        "acceptance_violations": acceptance_violations,
        "manual_overrides": overrides,
        "override_share": round(overrides / len(submissions_all), 3)
        if submissions_all else None,
        "recovery_heartbeat_timeouts": agent_timeouts,
        "blocked_by_limit": blocked_by_limit,
        "quarantined_workspaces": quarantined,
        "integration_conflicts": conflicts,
        "integration_events": integrated_events,
        "applied_runs": applied,
    }
    base.pop("recovered_tasks") if False else None
    return base


def eventlog_count(store, team: str, event_type: str) -> int:
    from . import events as eventlog

    return len(eventlog.list_events(store.conn, team,
                                    event_type=event_type))


def run_status(store, team: str) -> str | None:
    runs = store.all(
        "SELECT status FROM runs WHERE team_id = ? ORDER BY number DESC"
        " LIMIT 1", (team,))
    return runs[0]["status"] if runs else None
