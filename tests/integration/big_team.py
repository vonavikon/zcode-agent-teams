"""A team of the size the specification names (v2.1 spec 21): a hundred
tasks, twenty specialists, a journal of thousands of lines.

It is written straight into the team's database, through the storage
methods the engine itself uses, because driving a team of that size
through the operations would take minutes for what is a fixture. What
the engine would record is recorded: tasks in every state, specialists
at work, a journal with the events a real run writes, usage records
with and without a price.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_teams import engine
from agent_teams import events as eventlog
from agent_teams.storage import open_store

STATUSES = ["completed"] * 6 + ["in_progress"] * 2 + ["pending"] * 2
CYCLE = ("task_claimed", "agent_started", "task_submitted", "verification_failed",
         "task_submitted", "verification_passed", "task_completed", "token_usage_recorded")
MODELS = ["glm-5.3-flash", "glm-5.3", "gpt-5.2", None]        # None: no price


def spec_for(tasks: int, agents: int) -> dict:
    """``tasks`` stages in chains of ten (a stage depends on the one
    before it inside its chain), each given to one of ``agents``
    specialists, each with its own file."""
    return {
        "title": "Большой проект: сто этапов",
        "goal": "Проверить, что доска держит сто задач и двадцать специалистов",
        "config": {"workspace": "shared", "budget_usd": 5.0},
        "agents": [{"id": f"spec-{n + 1}", "role": "executor"} for n in range(agents)],
        "tasks": [
            {"id": f"stage-{n + 1:03d}", "title": f"Этап {n + 1}: сделать часть работы номер {n + 1}",
             "assignee": f"spec-{n % agents + 1}",
             "lens": ["behavior", "docs", "correctness", "security", "performance"][n % 5],
             "files_write": [f"out/part-{n + 1:03d}.md"], "acceptance": ["готово"],
             "deps": [f"stage-{n:03d}"] if n % 10 else []}
            for n in range(tasks)],
    }


def build_big_team(root: Path, *, team: str = "big", tasks: int = 100, agents: int = 20,
                   events: int = 5000, usage: int = 2000) -> dict:
    """Create the team and fill its journal; returns the lead token."""
    (root / "out").mkdir(parents=True, exist_ok=True)
    path = root / "big-spec.json"
    path.write_text(json.dumps(spec_for(tasks, agents), ensure_ascii=False), encoding="utf-8")
    lead = engine.op_init(root, team, path)["lead_token"]
    # The story ends now: the specialists that are at work answered a
    # moment ago, as in a team that really runs.
    total = tasks + 1 + events + usage
    start = datetime.now(timezone.utc) - timedelta(seconds=2 * total)

    def at(index: int) -> str:
        return (start + timedelta(seconds=2 * index)).isoformat(timespec="milliseconds") \
            .replace("+00:00", "Z")

    d = engine.team_dir_for(root, team)
    store = open_store(d)
    try:
        with store.transaction():
            run = store.one("SELECT id FROM runs WHERE team_id = ? ORDER BY number DESC LIMIT 1",
                            (team,))
            rows = store.all("SELECT id, assignee FROM tasks WHERE team_id = ? ORDER BY rowid", (team,))
            working: list[dict] = []          # the stages that are in work now
            answered = datetime.now(timezone.utc) - timedelta(seconds=20)

            def recent(moment: datetime) -> str:
                return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")

            for n, row in enumerate(rows):
                status = STATUSES[n % len(STATUSES)]
                done = status == "completed"
                started = status in ("in_progress", "completed")   # a pending stage has no owner
                if status == "in_progress":
                    working.append(row)
                store.run("UPDATE tasks SET status = ?, owner = ?, claimed_at = ?, submitted_at = ?,"
                          " completed_at = ?, accepted_commit_sha = ?, updated_at = ?, heartbeat_at = ?"
                          " WHERE team_id = ? AND id = ?",
                          (status, row["assignee"] if started else None, at(n) if started else None,
                           at(n) if done else None, at(n + 1) if done else None,
                           "0" * 40 if done else None, at(n),
                           # A specialist at work answered a moment ago.
                           recent(answered) if status == "in_progress" else None, team, row["id"]))
            busy: set[str] = set()
            for n, agent in enumerate(store.all("SELECT id FROM agents WHERE team_id = ?", (team,))):
                mine = next((row for row in working if row["assignee"] == agent["id"]), None)
                if mine:
                    busy.add(agent["id"])
                store.run("UPDATE agents SET status = ?, current_task = ?, heartbeat_at = ?"
                          " WHERE team_id = ? AND id = ?",
                          ("running" if mine else "idle", mine["id"] if mine else None, at(n),
                           team, agent["id"]))
            for k in range(events):
                row = rows[k % len(rows)]
                eventlog.append_event(
                    store.conn, event_type=CYCLE[k % len(CYCLE)], team_id=team, run_id=run["id"],
                    task_id=row["id"], actor_id=row["assignee"], timestamp=at(k),
                    metadata={"attempt": 1 + k // len(rows)})
            for u in range(usage):
                model = MODELS[u % len(MODELS)]
                tokens = 1000 + u % 9000
                store.add_usage({
                    "team_id": team, "run_id": run["id"], "task_id": rows[u % len(rows)]["id"],
                    "agent_id": f"spec-{u % agents + 1}", "model": model, "provider": "test",
                    "input_tokens": tokens * 3 // 4, "output_tokens": tokens // 4,
                    "total_tokens": tokens, "source": "manual", "recorded_at": at(events + u)})
            last_seq = store.one("SELECT MAX(seq) AS seq FROM events WHERE team_id = ?", (team,))["seq"]
    finally:
        store.close()
    return {"root": Path(root), "team": team, "lead": lead, "tasks": len(rows),
            "events": events, "usage": usage, "in_progress": len(working),
            "busy_agents": len(busy), "last_seq": last_seq}
