"""Read-only access to team state for the dashboard.

Every ``engine.op_*`` call opens the database read-write (it may migrate
and checkpoint). A polling dashboard must never do that, so this module
opens the database strictly read-only and returns one consistent
snapshot per call.

Nothing here can change team state: the connection is opened with
``mode=ro`` and ``PRAGMA query_only``, and only a whitelist of columns
leaves this module. Credential hashes never do, and free-text event
metadata is reduced to the fields a reader needs (spec v2.1, 16.2, 16.3).
The one file the board may show outside its own directory is a file of
a task's result, read exactly as the engine pinned it (``result_file``).
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import analytics, planview, viewmodel
from .config import merge_config
from .errors import EngineError
from .quality import integration_branch_name
from .storage import DB_FILENAME
from .validation import normalize_path

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
_SUBMISSION_COLUMNS = ("id, task_id, attempt, commit_sha, base_commit_sha, branch,"
                       " created_at, changed_files_json, verification_status, verifier,"
                       " evidence_status")
# Keys of tasks.metadata_json safe to expose. The rest may hold free text
# and is dropped.
_META_KEYS = ("changed_files", "workspace_violations", "quarantine_dir",
              "quarantined_at", "submission_id", "uncommitted_changes")
# Keys of event metadata safe to expose in the simple level. The remark of
# a checker is the one the reader is sent to when a task is blocked.
_EVENT_META_KEYS = ("reason", "violations", "conflicts", "restarts",
                    "override", "override_reason", "applied", "applied_at",
                    "feedback", "failures", "attempt")

# A file of a result the board shows as text, and one it shows as a
# picture. Anything else is offered as a download, never rendered: a
# result is the team's own content and may hold anything.
RESULT_TEXT = frozenset(".md .txt .json .csv .tsv .log .py .js .css .html .htm .xml"
                        " .yaml .yml .toml .ini .cfg .conf .sh .sql .rst .org .tex"
                        " .svg".split())
RESULT_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
                ".bmp": "image/bmp"}
RESULT_MAX_TEXT = 256 * 1024
RESULT_MAX_IMAGE = 5 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


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


# The busy first, then those in trouble, then the free, then the done.
_SPECIALIST_ORDER = {"working": 0, "verifying": 1, "problem": 2, "waiting": 3,
                     "recovering": 4, "free": 5, "done": 6}
_SPECIALIST_STATE_INDEX = {state["label"]: key for key, state in viewmodel.AGENT_STATE.items()}


def _specialist_order(specialist: dict) -> tuple:
    return (_SPECIALIST_ORDER.get(_SPECIALIST_STATE_INDEX.get(specialist["state"], "free"), 9),
            specialist["name"])


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


def _result(task: dict, submission: dict | None, level: str) -> dict:
    """The files a task produced, as its latest handed-over result (spec 8.5).

    A result is "ready" once the task is accepted. Before that the files
    are the intermediate result of the last hand-over, if there was one.
    """
    files = list((submission or {}).get("changed_files")
                 or (task.get("metadata") or {}).get("changed_files") or [])
    if not files and task.get("submitted_at"):
        # Shared mode keeps no diff of what changed: the files the task
        # declared are what the engine knows the result consists of.
        files = list(task.get("files_write") or [])
    view = {"files": files, "ready": task["status"] == "completed",
            "handed_over": bool(task.get("submitted_at")),
            "handed_over_count": (submission or {}).get("count", 0)}
    if level == "advanced":
        view["version"] = (submission or {}).get("commit_sha")
    return view


def snapshot(project_root: Path, team: str, *, level: str = "simple",
             event_limit: int = 200, event_after: int | None = None,
             event_before: int | None = None, parts: tuple[str, ...] = ()) -> dict:
    """Everything the dashboard needs, in one consistent read.

    ``level`` is ``simple`` or ``advanced``. The feed is a window of the
    journal: only events with a greater ``seq`` than ``event_after``
    (cursor-based polling) and a smaller one than ``event_before``
    (reading further back), the newest ``event_limit`` of them.
    ``parts`` asks for what the whole board does not need on every
    request: ``task_events`` (the history of each task, by task id).
    """
    if level not in ("simple", "advanced"):
        level = "simple"
    project_root = Path(project_root).resolve()
    now = datetime.now(timezone.utc)
    with _readonly(team_dir(project_root, team)) as conn:
        team_row = _decode(conn.execute(
            "SELECT id, project_root, created_at, status, base_commit,"
            " config_json, spec_json FROM teams WHERE id = ?", (team,)).fetchone())
        if team_row is None:
            raise DashboardDataError(f"team {team!r} not found")
        config = merge_config(team_row.get("config") or {})
        run = _decode(conn.execute(
            "SELECT id, number, started_at, finished_at, status,"
            " integration_base_commit, integration_commit_sha FROM runs"
            " WHERE team_id = ? ORDER BY number DESC LIMIT 1", (team,)).fetchone())
        tasks = [_decode(r) for r in conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE team_id = ? ORDER BY rowid",
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
            f"SELECT {_SUBMISSION_COLUMNS} FROM submissions WHERE team_id = ?"
            " ORDER BY created_at, rowid", (team,))]
        attempts = [_decode(r) for r in conn.execute(
            "SELECT task_id, attempt, verifier, actor, verdict, feedback,"
            " created_at, report_json FROM verification_attempts WHERE team_id = ?"
            " ORDER BY created_at, rowid", (team,))]
        raw_events = conn.execute(
            "SELECT seq, event_id, run_id, task_id, actor_id, event_type,"
            " timestamp, metadata_json, hash FROM events WHERE team_id = ?"
            " ORDER BY seq", (team,)).fetchall()

    for t in tasks:
        meta = t.get("metadata")
        t["metadata"] = {k: meta[k] for k in _META_KEYS
                         if isinstance(meta, dict) and k in meta}

    threshold = config["heartbeat_timeout_s"]
    titles = {t["id"]: t["title"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    first_task: dict[str, dict] = {}
    for t in tasks:
        first_task.setdefault(t.get("assignee"), t)
    # Two executors can have one role (two "AI-аналитик" in a plan). They
    # are told apart by a number, on their cards and on every line of
    # the feed, so the reader never takes two people for one.
    own = {a["id"]: viewmodel.role_name(first_task.get(a["id"])) for a in agents}
    numbered = viewmodel.number_duplicates(own)
    # (agent, task) -> the role that agent wears on that task; (agent,
    # None) -> the specialist as a whole, named by its first task.
    roles = {(agent_id, None): name for agent_id, name in numbered.items()}
    for t in tasks:
        who = t.get("assignee") or t.get("owner")
        if not who:
            continue
        role = viewmodel.role_name(t)
        roles[(who, t["id"])] = numbered[who] if role == own.get(who) else role
    # Spend is counted per role, with the specialists of one role summed.
    role_by_agent = dict(own)
    quarantined = {t["id"] for t in tasks if t["metadata"].get("quarantine_dir")}
    checks_by_task: dict[str, list[dict]] = {}
    remarks: dict[str, str] = {}
    for a in attempts:
        checks_by_task.setdefault(a["task_id"], []).append(a)
        if a.get("verdict") == "fail" and a.get("feedback"):
            remarks[a["task_id"]] = a["feedback"]
    latest = {}
    for s in submissions:
        s["count"] = latest.get(s["task_id"], {}).get("count", 0) + 1
        latest[s["task_id"]] = s            # the last one handed over, with their number

    # The checker is a specialist of the team too; the engine keeps no
    # agent row for one without tasks of its own.
    cards = [(a["id"], viewmodel.agent_view(a, tasks, level=level, now=now,
                                            threshold_s=threshold, name=numbered[a["id"]]))
             for a in agents]
    verifiers = tuple(sorted({a["verifier"] for a in attempts if a.get("verifier")}))
    # Tasks whose result the lead accepted by decision: an attempt the
    # engine records as passed, and not the checker's doing.
    accepted_by_lead = set()
    for row in raw_events:
        if row["event_type"] != "verification_passed":
            continue
        meta = _decode(row).get("metadata")
        if isinstance(meta, dict) and meta.get("override"):
            accepted_by_lead.add(row["task_id"])
    if attempts or any(t["status"] == "awaiting_verification" for t in tasks):
        checker = viewmodel.verifier_view(tasks, attempts, accepted_by_lead)
        if level == "advanced":
            checker["tech"] = {"verifiers": list(verifiers)}
        cards.append((verifiers, checker))
    cards.sort(key=lambda card: _specialist_order(card[1]))
    # Which card an event's actor belongs to, as a position (never an id).
    index_of = {agent_id: position for position, (ids, _view) in enumerate(cards)
                for agent_id in (ids if isinstance(ids, tuple) else (ids,))}

    # One pass over the journal, oldest first: the feed, the history of
    # every task and the timeline all come from this one list.
    events, task_events, last_by_task = [], {}, {}
    overridden: dict[str, str] = {}       # submission id -> task id, manual acceptance
    conflict_meta = None                  # the latest clash of the integration, as the engine wrote it
    merged = None                         # the commits the integration merged
    for row in raw_events:
        e = _decode(row)
        raw_meta = e.get("metadata")
        if isinstance(raw_meta, dict):
            if e.get("event_type") == "workspace_conflict" and raw_meta.get("conflicts"):
                conflict_meta = raw_meta
            elif e.get("event_type") == "workspace_integrated":
                merged = raw_meta.get("merged")
        e["metadata"] = _event_metadata(raw_meta, level)
        after_override = False
        if e.get("event_type") == "verification_passed" and isinstance(raw_meta, dict):
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
                task_events.setdefault(e["task_id"], []).append(item)
                last_by_task[e["task_id"]] = item
    # The cursor follows the journal, not the filtered feed: the newest
    # events may all be hidden ones, and a cursor that stopped before
    # them would ask for the same empty page forever.
    last_seq = raw_events[-1]["seq"] if raw_events else (event_after or 0)
    window = [item for item in events
              if (event_after is None or item["seq"] > event_after)
              and (event_before is None or item["seq"] < event_before)]
    feed = window[-event_limit:] if event_limit > 0 else []

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

    # The clock of the team stops with its run; a finished team is not
    # "in work for three days".
    started = viewmodel.parse_time(team_row.get("created_at"))
    end = now
    if run and run.get("finished_at"):
        end = viewmodel.parse_time(run["finished_at"]) or now
    now_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    task_views = []
    for t in tasks:
        view = viewmodel.task_view(
            t, level=level, now=now,
            attempts=_checks(checks_by_task.get(t["id"]), t["id"] in accepted_by_lead),
            titles=titles, threshold_s=threshold, by_id=by_id)
        newest = last_by_task.get(t["id"])
        if newest is not None:      # the newest line of the feed about it (spec 4.3)
            view["last_event"] = {"title": newest["title"],
                                  "detail": newest.get("detail", ""), "ts": newest.get("ts")}
        view["result"] = _result(t, latest.get(t["id"]), level)
        task_views.append(view)

    cost = viewmodel.cost_view(usage, task_of=titles, role_of=role_by_agent)
    cost["series"] = analytics.spend_series(usage)
    quality = analytics.quality_view(tasks, attempts, accepted_by_lead, titles)
    budget = analytics.budget_view(cost, config, tasks)
    budget["series"] = cost["series"]
    if level == "advanced":
        # Tokens and the price per model (spec 11.3), from the records as stored.
        cost["models"] = analytics.model_rows(usage)
    integration = analytics.integration_view(tasks, run, events)
    if level == "advanced":
        # The exact versions for the technical reader (spec 13): where
        # the integration started, what it merged, what it made, and the
        # clash itself as the engine recorded it.
        integration["tech"] = {"run_id": run["id"] if run else None,
                               "status": run["status"] if run else None,
                               "branch": integration_branch_name(team),
                               "base_commit": run.get("integration_base_commit") if run else None,
                               "commit_sha": run.get("integration_commit_sha") if run else None,
                               "merged": merged, "conflict": conflict_meta}

    out = {
        "team": team,
        "created_at": team_row["created_at"],
        "now": now_iso,
        # No readable start time: zero, which is at least not a lie.
        "elapsed_s": max(0, int((end - started).total_seconds())) if started else 0,
        "finished": bool(run and run.get("finished_at")),
        "run": run_view,
        "level": level,
        "project": project_view(team_row, project_root, level),
        "progress": viewmodel.progress(tasks),
        "tasks": task_views,
        "agents": [view for _ids, view in cards],
        "attention": viewmodel.attention_items(
            tasks, run, now=now, threshold_s=threshold, remarks=remarks,
            budget=budget),
        "events": feed,
        "last_seq": last_seq,
        "cost": cost,
        # What the numbers mean (v2.1 spec 10 to 13).
        "quality": quality,
        "budget": budget,
        "recovery": analytics.recovery_view(tasks, task_events, budget),
        "integration": integration,
        # The plan as a graph (spec 6) and the time of every task (spec 7),
        # both drawn only from what the engine recorded.
        "plan": planview.plan_layout(tasks),
        "timeline": planview.timeline(
            task_views, task_events, team_row["created_at"],
            end.isoformat(timespec="milliseconds").replace("+00:00", "Z")),
    }
    comparison = benchmark_view(project_root, team, config)
    if comparison is not None:
        out["benchmark"] = comparison
    if level == "advanced":
        out["tech"] = {"submissions": [{k: v for k, v in s.items() if k != "count"}
                                       for s in submissions],
                       "attempts": attempts, "usage": usage, "config": config}
    if "task_events" in parts:
        out["task_events"] = task_events
    return out


def result_file(project_root: Path, team: str, task_id: str, name: str
                ) -> tuple[bytes, str] | None:
    """One file of a task's result, as the engine pinned it (spec 8.5).

    Only a file the task's latest hand-over consists of can be read, and
    only as that hand-over pinned it: from its commit in a git project,
    or from the project folder in shared mode. Nothing else on the disk
    is reachable through the board. Returns the bytes and a content type,
    or None when there is no such file.
    """
    root = Path(project_root).resolve()
    with _readonly(team_dir(root, team)) as conn:
        task = _decode(conn.execute(
            "SELECT metadata_json, files_write_json FROM tasks"
            " WHERE team_id = ? AND id = ?", (team, task_id)).fetchone())
        if task is None:
            return None
        row = _decode(conn.execute(
            "SELECT commit_sha, changed_files_json FROM submissions"
            " WHERE team_id = ? AND task_id = ? ORDER BY created_at DESC, rowid DESC"
            " LIMIT 1", (team, task_id)).fetchone()) or {}
    files = (row.get("changed_files") or (task.get("metadata") or {}).get("changed_files")
             or [])
    if not files and not row.get("commit_sha"):
        # Shared mode keeps neither a version nor a diff of what changed:
        # the files the task declared are what the engine knows of.
        files = task.get("files_write") or []
    row = {"commit_sha": row.get("commit_sha"), "changed_files": files}
    if name not in (row.get("changed_files") or []):
        return None
    try:
        normalize_path(root, name, check_fs=False)
    except EngineError:
        return None
    kind = ("image" if Path(name).suffix.lower() in RESULT_IMAGE
            else "text" if Path(name).suffix.lower() in RESULT_TEXT else "other")
    sha = row.get("commit_sha") or ""
    try:
        if _SHA.match(sha):
            size = subprocess.run(["git", "-C", str(root), "cat-file", "-s", f"{sha}:{name}"],
                                  capture_output=True, timeout=20, check=True).stdout
            if int(size) > (RESULT_MAX_IMAGE if kind == "image" else RESULT_MAX_TEXT):
                raise DashboardDataError(f"{name} is too large to show on the board")
            data = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{name}"],
                                  capture_output=True, timeout=20, check=True).stdout
        else:
            # Shared mode: no commit to pin. The file where the team works.
            path = (root / name).resolve()
            if root not in path.parents or not path.is_file():
                return None
            if path.stat().st_size > (RESULT_MAX_IMAGE if kind == "image" else RESULT_MAX_TEXT):
                raise DashboardDataError(f"{name} is too large to show on the board")
            data = path.read_bytes()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise DashboardDataError(f"cannot read {name}: {exc}") from exc
    if kind == "image":
        return data, RESULT_IMAGE[Path(name).suffix.lower()]
    # Text is shown as text, never as a page of its own: a result is the
    # team's content, and the board does not run it. Anything else is a
    # download.
    if kind == "other":
        return data, "application/octet-stream"
    return data, "text/plain; charset=utf-8"


def benchmark_view(project_root: Path, team: str, config: dict) -> dict | None:
    """The teams of this project that ran one declared set of tasks
    (v2.1 spec 19), or None when this team declares no benchmark.

    Every team of the project is read the same read-only way this one
    was. Only teams that declare the same benchmark name are compared.
    """
    declared = config.get("benchmark")
    if not declared:
        return None
    root = Path(project_root).resolve()
    rows = []
    for path in sorted((root / TEAMS_DIR).glob(f"*/{DB_FILENAME}")):
        try:
            with _readonly(path.parent) as conn:
                row = _decode(conn.execute(
                    "SELECT id, created_at, config_json FROM teams LIMIT 1").fetchone())
                if row is None or ((row.get("config") or {}).get("benchmark") or {}).get("name") \
                        != declared["name"]:
                    continue
                tasks = [_decode(r) for r in conn.execute(
                    "SELECT id, title, status, claimed_at, completed_at, submitted_at"
                    " FROM tasks WHERE team_id = ?", (row["id"],))]
                usage = [_decode(r) for r in conn.execute(
                    "SELECT task_id, agent_id, total_tokens, cost_usd, cost_quality"
                    " FROM token_usage WHERE team_id = ?", (row["id"],))]
                attempts = [_decode(r) for r in conn.execute(
                    "SELECT task_id, verdict, feedback, created_at FROM verification_attempts"
                    " WHERE team_id = ? ORDER BY created_at, rowid", (row["id"],))]
                manual, problems, conflicts = set(), 0, 0
                for event in conn.execute(
                        "SELECT event_type, task_id, metadata_json FROM events WHERE team_id = ?",
                        (row["id"],)):
                    meta = json.loads(event["metadata_json"]) \
                        if isinstance(event["metadata_json"], str) else event["metadata_json"]
                    meta = meta if isinstance(meta, dict) else {}
                    if event["event_type"] == "verification_passed" and meta.get("override"):
                        manual.add(event["task_id"])
                    elif event["event_type"] == "workspace_conflict":
                        conflicts += 1
                    elif event["event_type"] in analytics.PROBLEM_EVENTS:
                        problems += 1
        except (DashboardDataError, sqlite3.Error, KeyError, TypeError, ValueError):
            continue                    # a team that cannot be read is not compared
        live = [t for t in tasks if t["status"] != "cancelled"]
        starts = [p for p in (viewmodel.parse_time(t.get("claimed_at")) for t in tasks) if p]
        ends = [p for p in (viewmodel.parse_time(t.get("completed_at")) for t in tasks) if p]
        # Finished for a comparison: every task that counts was accepted.
        # Whether the result was then applied to the project is a step of
        # its own and not part of the measured work.
        finished = bool(live and all(t["status"] == "completed" for t in live))
        duration = round((max(ends) - min(starts)).total_seconds(), 3) if starts and ends else 0
        cost = viewmodel.cost_view(usage)
        quality = analytics.quality_view(tasks, attempts, manual)
        rows.append({
            "team": row["id"], "executors": ((row.get("config") or {}).get("benchmark") or {}).get("executors"),
            "finished": finished, "duration_s": duration if finished else None,
            "spent_usd": cost["usd"], "cost_complete": cost["complete"],
            "accepted": len([t for t in live if t["status"] == "completed"]), "total": len(live),
            "first_time": quality["first_time"], "judged": quality["judged"],
            # Attempts and problems (spec 19): checks by the checker (a
            # manual acceptance is not an attempt), the problems the
            # recovery centre counts, and clashes between results.
            "attempts": len(attempts) - len(manual), "problems": problems,
            "conflicts": conflicts, "reworked": quality["reworked"],
        })
    return analytics.benchmark_compare(str(declared["name"]), rows, team)
