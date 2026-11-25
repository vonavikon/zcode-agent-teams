"""Read-only access to team state for the dashboard.

Every ``engine.op_*`` call opens the database read-write (it may migrate
and checkpoint). A polling dashboard must never do that, so this module
opens the database strictly read-only and returns one consistent
snapshot per call.

Nothing here can change team state: the connection is opened with
``mode=ro`` and ``PRAGMA query_only``, and only a whitelist of columns
leaves this module. Credential hashes never do, and free-text event
metadata is reduced to the fields a reader needs (spec v2.1, 16.2, 16.3).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import viewmodel
from .config import merge_config
from .storage import DB_FILENAME

TEAMS_DIR = ".agent-teams"

# Columns of tasks the dashboard may see. Token hashes are not listed and
# cannot be requested: the SELECT names every column explicitly.
_TASK_COLUMNS = (
    "id, title, status, assignee, owner, lens, deps_json, attempt,"
    " verification_failures, restarts, claimed_at, submitted_at,"
    " completed_at, blocked_at, cancelled_at, blocked_reason,"
    " heartbeat_at, lease_expires_at, base_commit, head_commit, branch,"
    " workspace_path, trusted_base_commit, draft_checkpoint_commit,"
    " accepted_commit_sha, dependency_base_json, files_read_json,"
    " files_write_json, acceptance_json, tokens, metadata_json,"
    " created_at, updated_at, spec_json")
_AGENT_COLUMNS = ("id, role, status, current_task, started_at, finished_at,"
                  " heartbeat_at, restarts")
# Keys of tasks.metadata_json safe to expose. The rest may hold free text
# and is dropped.
_META_KEYS = ("changed_files", "workspace_violations", "quarantine_dir",
              "quarantined_at", "submission_id", "uncommitted_changes")
# Keys of event metadata safe to expose in the simple level. The remark of
# a checker is the one the reader is sent to when a task is blocked.
_EVENT_META_KEYS = ("reason", "violations", "conflicts", "restarts",
                    "override", "override_reason", "applied", "applied_at",
                    "feedback", "failures", "attempt")


class DashboardDataError(Exception):
    """The team state cannot be read (no database, unusable file)."""


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = {}
    for key in row.keys():
        value = row[key]
        if key.endswith("_json"):
            key = key[:-len("_json")]
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except json.JSONDecodeError:
                value = None
        out[key] = value
    return out


@contextmanager
def _readonly(team_dir: Path):
    """A read-only connection to one team's state.db.

    WAL readers need the -shm sidecar, which a ``mode=ro`` connection can
    create when it is missing; that is SQLite's own bookkeeping, not team
    state, and it is what lets reads run while the engine writes.
    """
    path = team_dir / DB_FILENAME
    if not path.exists():
        raise DashboardDataError(f"no team state at {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        # One snapshot for all the reads of one request.
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise DashboardDataError(f"cannot read team state: {exc}") from exc
    finally:
        conn.close()


def team_dir(project_root: Path, team: str) -> Path:
    return Path(project_root).resolve() / TEAMS_DIR / team


_SPECIALIST_ORDER = {"working": 0, "verifying": 1, "problem": 2, "waiting": 3,
                     "recovering": 4, "free": 5, "done": 6}
_SPECIALIST_STATE_INDEX = {label: key for key, state in viewmodel.AGENT_STATE.items()
                           for label in [state["label"]]}


def _specialist_order(specialist: dict) -> tuple:
    """The busy first, then those in trouble, then the free, then the done."""
    return (_SPECIALIST_ORDER.get(_SPECIALIST_STATE_INDEX.get(specialist["state"], "free"), 9),
            specialist["name"])


def with_last_event(task: dict, event: dict | None) -> dict:
    """The newest line of the feed about this task, for its card (spec 4.3)."""
    if event is not None:
        task["last_event"] = {"title": event["title"], "detail": event.get("detail", ""),
                              "ts": event.get("ts")}
    return task


def project_view(team_row: dict, project_root: Path, level: str) -> dict:
    """The name and the goal the reader gave the team (spec 4.1).

    Both are optional words of the spec. A team without a name is called
    by the name of its project folder; the full path is for the
    technical level only.
    """
    spec = team_row.get("spec") or {}
    view = {"name": str(spec.get("title") or "").strip() or Path(project_root).name,
            "goal": str(spec.get("goal") or "").strip(),
            "started_at": team_row.get("created_at")}
    if level == "advanced":
        view["root"] = str(project_root)
    return view


def _checks(attempts: list[dict] | None, manual_pass: bool) -> list[dict] | None:
    """Verification attempts of a task, with the override marked.

    ``manual_pass`` says the task's accepted result was accepted by the
    lead's decision. The engine stores no flag for it on the attempt,
    so the last passed one is the one to mark.
    """
    if attempts is None:
        return None
    marked = [dict(a) for a in attempts]
    if manual_pass:
        for a in reversed(marked):
            if a.get("verdict") == "pass":
                a["manual"] = True
                break
    return marked


def _event_metadata(raw, level: str) -> dict:
    """Event metadata cut down to what a simple reader needs.

    The advanced level gets everything: event metadata never holds a
    credential (the engine keeps the lead token and the execution tokens
    only as hashes in ``teams`` and ``tasks``), and the technical reader
    needs the transitions.
    """
    if not isinstance(raw, dict):
        return {}
    if level == "advanced":
        return dict(raw)
    return {k: v for k, v in raw.items() if k in _EVENT_META_KEYS}


def snapshot(project_root: Path, team: str, *, level: str = "simple",
             event_limit: int = 200, event_after: int | None = None,
             parts: tuple[str, ...] = ()) -> dict:
    """Everything the dashboard needs, in one consistent read.

    ``level`` is ``simple`` or ``advanced``; ``event_after`` returns only
    events with a greater ``seq`` (cursor-based polling). ``parts`` asks
    for what the whole board does not need on every poll: ``task_events``
    (the history of each task, by task id) for the page of one task.
    """
    if level not in ("simple", "advanced"):
        level = "simple"
    now = datetime.now(timezone.utc)
    tdir = team_dir(project_root, team)
    with _readonly(tdir) as conn:
        team_row = _decode(conn.execute(
            "SELECT id, project_root, created_at, status, base_commit,"
            " config_json, spec_json FROM teams WHERE id = ?", (team,)).fetchone())
        if team_row is None:
            raise DashboardDataError(f"team {team!r} not found")
        run = _decode(conn.execute(
            "SELECT id, number, started_at, finished_at, status,"
            " integration_base_commit, integration_commit_sha FROM runs"
            " WHERE team_id = ? ORDER BY number DESC LIMIT 1", (team,)).fetchone())
        tasks = [_decode(r) for r in conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE team_id = ? ORDER BY id",
            (team,))]
        agents = [_decode(r) for r in conn.execute(
            f"SELECT {_AGENT_COLUMNS} FROM agents WHERE team_id = ? ORDER BY id",
            (team,))]
        usage = [_decode(r) for r in conn.execute(
            "SELECT agent_id, task_id, model, provider, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens,"
            " total_tokens, tool_uses, duration_ms, cost_usd, cost_quality,"
            " source, recorded_at FROM token_usage WHERE team_id = ?"
            " ORDER BY recorded_at, id", (team,))]
        submissions = [_decode(r) for r in conn.execute(
            "SELECT id, task_id, attempt, commit_sha, base_commit_sha, branch,"
            " created_at, changed_files_json, verification_status, verifier"
            " FROM submissions WHERE team_id = ? ORDER BY created_at, id",
            (team,))]
        attempts = [_decode(r) for r in conn.execute(
            "SELECT task_id, attempt, verifier, actor, verdict, feedback,"
            " created_at FROM verification_attempts WHERE team_id = ?"
            " ORDER BY task_id, attempt", (team,))]
        cursor_sql = " AND seq > ?" if event_after is not None else ""
        cursor_args: tuple = (team, event_after) if event_after is not None else (team,)
        raw_events = conn.execute(
            "SELECT seq, event_id, run_id, task_id, actor_id, event_type,"
            " timestamp, metadata_json, hash FROM events WHERE team_id = ?"
            f"{cursor_sql} ORDER BY seq DESC LIMIT ?", (*cursor_args, event_limit)
        ).fetchall()

    for t in tasks:
        meta = t.get("metadata")
        t["metadata"] = {k: meta[k] for k in _META_KEYS
                         if isinstance(meta, dict) and k in meta}

    # Id to name: the simple level shows roles and titles, never ids.
    threshold = merge_config(team_row.get("config") or {})["heartbeat_timeout_s"]
    titles = {t["id"]: t["title"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    first_task: dict[str, dict] = {}
    for t in tasks:
        first_task.setdefault(t.get("assignee"), t)
    # Tasks whose result the lead accepted by decision: found once, read
    # twice (the pairing of the journal events and the checker's count).
    accepted_by_lead = {row["task_id"] for row in raw_events
                        if row["event_type"] == "verification_passed"
                        and isinstance(_decode(row).get("metadata"), dict)
                        and (_decode(row).get("metadata") or {}).get("override")}
    # (agent, task) -> the role that agent wears on that task; (agent,
    # None) -> the specialist as a whole, named by its first task.
    roles = {(a["id"], None): viewmodel.role_name(first_task.get(a["id"])) for a in agents}
    roles.update({(t.get("assignee") or t.get("owner"), t["id"]): viewmodel.role_name(t)
                  for t in tasks if t.get("assignee") or t.get("owner")})
    role_by_agent = {agent_id: name for (agent_id, task_id), name in roles.items()
                     if task_id is None}
    quarantined = {t["id"] for t in tasks if t["metadata"].get("quarantine_dir")}
    checks_by_task: dict[str, list[dict]] = {}
    remarks: dict[str, str] = {}
    for a in attempts:
        checks_by_task.setdefault(a["task_id"], []).append(a)
        if a.get("verdict") == "fail" and a.get("feedback"):
            remarks[a["task_id"]] = a["feedback"]

    # The checker is a specialist of the team too; the engine keeps no
    # agent row for one without tasks of its own.
    cards = [(a["id"], viewmodel.agent_view(a, tasks, level=level, now=now,
                                            threshold_s=threshold)) for a in agents]
    verifiers = tuple(sorted({a["verifier"] for a in attempts if a.get("verifier")}))
    if attempts or any(t["status"] == "awaiting_verification" for t in tasks):
        checker = viewmodel.verifier_view(tasks, attempts, accepted_by_lead)
        if level == "advanced":
            checker["tech"] = {"verifiers": list(verifiers)}
        cards.append((verifiers, checker))
    cards.sort(key=lambda card: _specialist_order(card[1]))
    # Which card an event's actor belongs to, as a position (never an id).
    index_of = {agent_id: position for position, (ids, _view) in enumerate(cards)
                for agent_id in (ids if isinstance(ids, tuple) else (ids,))}

    events = []
    last_by_task: dict[str, dict] = {}
    task_events: dict[str, list[dict]] = {}
    overridden: dict[str, str] = {}  # submission id -> task id, manual acceptance
    for row in reversed(raw_events):  # oldest first, the order of a story
        e = _decode(row)
        raw_meta = e.get("metadata")
        e["metadata"] = _event_metadata(raw_meta, level)
        after_override = False
        if (e.get("event_type") == "verification_passed"
                and isinstance(raw_meta, dict)):
            sid = raw_meta.get("submission_id")
            if raw_meta.get("override") and sid:
                overridden[sid] = e.get("task_id")
            elif sid and sid in overridden:
                # The state change the decision above caused: same type
                # as a passed check, no check behind it.
                after_override = True
        item = viewmodel.event_view(
            e, level=level, titles=titles, roles=roles,
            quarantined=e.get("task_id") in quarantined,
            after_override=after_override)
        if item is not None:
            item["by"] = index_of.get(e.get("actor_id"))
            events.append(item)
            if e.get("task_id") is not None:
                last_by_task[e["task_id"]] = item
                task_events.setdefault(e["task_id"], []).append(item)
    # The cursor follows the journal, not the filtered feed: the newest
    # events may all be hidden ones, and a cursor that stopped before
    # them would ask for the same empty page forever.
    last_seq = raw_events[0]["seq"] if raw_events else (event_after or 0)

    # A check history must not show a passed check for a manual acceptance.
    manual_tasks = set(overridden.values())

    run_view = None
    if run:
        state = viewmodel.RUN_STATE.get(run["status"], viewmodel.RUN_STATE["active"])
        run_view = {"state": state["label"], "hint": state["hint"],
                    "number": run["number"], "started_at": run["started_at"],
                    "finished_at": run.get("finished_at")}
        if level == "advanced":
            run_view["tech"] = {"run_id": run["id"], "status": run["status"],
                                "integration_base_commit": run.get("integration_base_commit"),
                                "integration_commit_sha": run.get("integration_commit_sha")}

    specialists = [view for _ids, view in cards]

    # The clock of the team stops with its run; a finished team is not
    # "in work for three days".
    started = viewmodel.parse_time(team_row.get("created_at"))
    end = now
    if run and run.get("finished_at"):
        end = viewmodel.parse_time(run["finished_at"]) or now

    out = {
        "team": team,
        "created_at": team_row["created_at"],
        "now": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        # No readable start time: zero, which is at least not a lie.
        "elapsed_s": max(0, int((end - started).total_seconds())) if started else 0,
        "finished": bool(run and run.get("finished_at")),
        "run": run_view,
        "level": level,
        "project": project_view(team_row, project_root, level),
        "progress": viewmodel.progress(tasks),
        "tasks": [with_last_event(viewmodel.task_view(
            t, level=level, now=now,
            attempts=_checks(checks_by_task.get(t["id"]), t["id"] in manual_tasks),
            titles=titles, threshold_s=threshold, by_id=by_id), last_by_task.get(t["id"]))
            for t in tasks],
        "agents": specialists,
        "attention": viewmodel.attention_items(
            tasks, run, now=now, threshold_s=threshold, remarks=remarks),
        "events": events,
        "last_seq": last_seq,
        "cost": viewmodel.cost_view(usage, task_of=titles, role_of=role_by_agent),
    }
    if level == "advanced":
        out["tech"] = {"submissions": submissions, "attempts": attempts,
                       "usage": usage}
    if "task_events" in parts:
        out["task_events"] = task_events
    return out
