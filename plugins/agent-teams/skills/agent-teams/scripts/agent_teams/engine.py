"""Engine operations: one function per CLI command, v0.3 (spec stage 1).

Every mutation runs under the team's StateLock and one Store transaction,
goes through the state machine, appends hash-chained events and afterwards
refreshes the v0.2-compatible mirrors (tasks.json, inbox/<agent>.json,
events.jsonl).

Authority model (spec section 6): the lead token printed once by init
authorizes lead-side verdicts and interventions; an execution token
printed once by claim authorizes the executor's submit. Free-text --by
values are recorded as actors, never trusted as authority.

Versions of a task (v2.1 spec 3.2, 3.3), each bound to a SHA. The
trusted base is what an attempt is checked against: the composition of
the parents' accepted commits, or the run base. The draft checkpoint is
what the last failed attempt left, kept to continue the work and never
trusted. The accepted SHA is the result the gate accepted.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import string
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import events as eventlog
from . import statemachine
from .config import merge_config
from .errors import (BudgetExceeded, NotFoundError, PermissionDenied, StateRejected,
                     UsageError)
from .locking import StateLock
from .storage import Store, now_iso, open_store

import agent_teams.workspaces as _ws


def workspaces_ref():
    return _ws

from .validation import (NAME_RE, normalize_acceptance, topological_order,
                         validate_team_spec)

TEAMS_DIR = ".agent-teams"


# ---------------------------------------------------------------- helpers


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def team_dir_for(root: Path, team: str) -> Path:
    if not NAME_RE.match(team or ""):
        raise UsageError(f"invalid team name: {team!r} "
                         "(pattern ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$)")
    return Path(root).resolve() / TEAMS_DIR / team


def _open_team(root: Path, team: str) -> tuple[Store, Path, dict]:
    d = team_dir_for(root, team)
    if not (d / "state.db").exists():
        raise NotFoundError(f"team not found: {d} (run init first)")
    store = open_store(d)
    row = store.get_team(team)
    if row is None:
        store.close()
        raise NotFoundError(f"team not found in state database: {team}")
    return store, d, row


def _require_lead(team_row: dict, lead_token: str | None) -> None:
    if not lead_token:
        raise PermissionDenied(
            "this command requires --lead-token (printed once by init)")
    if _sha256(lead_token) != team_row.get("lead_token_hash"):
        raise PermissionDenied("invalid lead token")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _current_run(store: Store, team_id: str, *, create: bool = False) -> dict | None:
    runs = store.all(
        "SELECT * FROM runs WHERE team_id = ? ORDER BY number DESC LIMIT 1",
        (team_id,))
    if runs and runs[0]["status"] != "finished":
        return runs[0]
    if not create:
        return runs[0] if runs else None
    number = (runs[0]["number"] + 1) if runs else 1
    run = {"id": uuid.uuid4().hex, "team_id": team_id, "number": number,
           "started_at": now_iso(), "status": "active", "metadata": {}}
    store.create_run(run)
    eventlog.append_event(store.conn, event_type="run_started", team_id=team_id,
                          run_id=run["id"], actor_id="lead",
                          metadata={"number": number})
    return run


def _mailbox(store: Store, team_id: str, run_id: str, to_agent: str,
             from_agent: str, task_id: str | None, kind: str, text: str) -> dict:
    return store.add_message({
        "id": uuid.uuid4().hex[:12], "team_id": team_id, "run_id": run_id,
        "from_agent": from_agent, "to_agent": to_agent, "task_id": task_id,
        "kind": kind, "text": text, "created_at": now_iso(), "read_at": None})


def _recorded(store: Store, team_id: str, rows: list[dict]) -> None:
    """Append the JSONL mirror for events recorded in this transaction."""
    for row in rows:
        if row.get("event_type") in ("run_started",):
            continue
        eventlog.append_jsonl(store.team_dir, row)


def _refresh_mirrors(store: Store, team_id: str) -> None:
    """Rewrite tasks.json and inbox/*.json in the v0.2 shapes."""
    team = store.get_team(team_id)
    tasks = store.list_tasks(team_id)
    payload = {
        "team": team_id,
        "created": team["created_at"],
        "tasks": [{
            "id": t["id"], "title": t["title"], "assignee": t["assignee"],
            "lens": t["lens"], "files_read": t["files_read"],
            "files_write": t["files_write"],
            "acceptance": [c.get("description", "") if isinstance(c, str) else c
                           for c in t["acceptance"]],
            "deps": t["deps"],
            "status": t["status"],
            "rounds": t["verification_failures"],
            "claimed_at": t["claimed_at"], "completed_at": t["completed_at"],
            "last_event_ts": t["last_event_ts"], "tokens": t["tokens"],
        } for t in tasks],
    }
    _write_json(store.team_dir / "tasks.json", payload)
    inbox = store.team_dir / "inbox"
    inbox.mkdir(exist_ok=True)
    addressees = {m["to_agent"] for m in store.list_messages(team_id)}
    for agent in addressees:
        rows = store.list_messages(team_id, to_agent=agent)
        _write_json(inbox / f"{agent}.json", {"messages": [{
            "id": m["id"], "from": m["from_agent"], "to": m["to_agent"],
            "text": m["text"], "ts": m["created_at"],
            "read": m["read_at"] is not None,
        } for m in rows]})


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.parent / f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _collected_issues(result) -> str:
    lines = []
    for issue in result.errors:
        where = f" {issue.path}" if issue.path else ""
        lines.append(f"- [{issue.code}] {issue.message}{where}")
    for issue in result.warnings:
        where = f" {issue.path}" if issue.path else ""
        lines.append(f"- [warning {issue.code}] {issue.message}{where}")
    return "\n".join(lines)


# ---------------------------------------------------------------- init


def op_init(root: Path, team: str, tasks_path: Path, *,
            check_fs: bool = True) -> dict:
    root = Path(root).resolve()
    spec = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    result = validate_team_spec(spec, root, check_fs=check_fs)
    if not result.ok:
        raise UsageError(
            "the team specification is invalid, nothing was created:\n"
            + _collected_issues(result),
            detail={"errors": [i.code for i in result.errors]})
    d = team_dir_for(root, team)
    if d.exists():
        raise UsageError(f"team directory already exists: {d}")
    config = merge_config(spec.get("config"))
    lead_token = _new_token()

    from . import workspaces

    workspace_note = None
    if config["workspace"] == "shared":
        workspace_note = ("workspace mode 'shared': executors write directly "
                          "into the project, there is NO isolation")
    elif config["workspace"] == "auto" and not workspaces.is_git_project(root):
        workspace_note = ("no git repository found: workspace falls back to "
                          "'shared', executors write directly into the project "
                          "and there is NO isolation")
    elif config["workspace"] == "worktree" and not workspaces.is_git_project(root):
        raise UsageError(
            "config.workspace is 'worktree' but the project is not a git "
            "repository: use 'auto' or 'shared'")
    if workspace_note:
        result_warnings = [workspace_note]
    else:
        result_warnings = []

    run_base = None
    if workspaces_ref().is_git_project(root):
        run_base = workspaces_ref()._git(["rev-parse", "HEAD"],
                                         root).stdout.strip()

    d.mkdir(parents=True)
    with open_store(d) as store:
        with StateLock(d, "init:lead"), store.transaction():
            store.create_team({"id": team, "project_root": str(root),
                               "base_commit": run_base,
                               "config": config, "spec": spec,
                               "created_at": now_iso()})
            store.update_team(team, lead_token_hash=_sha256(lead_token))
            recorded = [eventlog.append_event(
                store.conn, event_type="team_created", team_id=team,
                run_id="", actor_id="lead",
                metadata={"tasks": len(spec["tasks"]),
                          "teammates": sorted({t["assignee"]
                                               for t in spec["tasks"]})})]
            run = _current_run(store, team, create=True)
            recorded.append(_last_event(store, team))
            declared = {a["id"]: a.get("role", "executor")
                        for a in spec.get("agents", [])}
            for agent_id in sorted({t["assignee"] for t in spec["tasks"]} |
                                   set(declared)):
                store.upsert_agent({"team_id": team, "id": agent_id,
                                    "role": declared.get(agent_id, "executor"),
                                    "status": "idle", "started_at": now_iso()})
            for raw in spec["tasks"]:
                acceptance = normalize_acceptance(raw["acceptance"])
                store.upsert_task({
                    "team_id": team, "id": raw["id"], "title": raw["title"],
                    "assignee": raw["assignee"], "lens": raw.get("lens", ""),
                    "files_read": raw.get("files_read", []),
                    "files_write": raw.get("files_write", []),
                    "acceptance": acceptance, "deps": raw.get("deps", []),
                    "spec": raw, "status": "pending"})
                recorded.append(eventlog.append_event(
                    store.conn, event_type="task_created", team_id=team,
                    run_id=run["id"], task_id=raw["id"], actor_id="lead",
                    metadata={"assignee": raw["assignee"]}))
            _recorded(store, team, recorded)
        _refresh_mirrors(store, team)
    return {"team": team, "tasks": len(spec["tasks"]), "dir": str(d),
            "lead_token": lead_token, "run_base": run_base,
            "warnings": [i.message for i in result.warnings] + result_warnings}


def _new_token() -> str:
    """A lead token that a command line can carry.

    ``secrets.token_urlsafe`` may start with a dash, and a value like
    ``-abc`` after ``--lead-token`` reads as the next option: the
    command then refuses its own token. That happened to one team in 64
    and surfaced as a test failing at random.
    """
    return secrets.choice(string.ascii_letters + string.digits) + secrets.token_urlsafe(23)


def _known_spend(store: Store, team: str, task_id: str | None = None) -> float:
    """Money the engine knows the team (or one task of it) has spent.

    Records without a price are not a zero: they are left out here, and
    the board says that the limit is watched over the known part only.
    """
    from .observability import cost_of

    return round(sum(cost_of(record)[0] or 0.0
                     for record in store.list_usage(team, task_id=task_id)), 6)


def _usd(value: float) -> str:
    """Money for a message: cents when there are any, else the small sum as it is."""
    return f"${value:,.2f}" if value >= 0.01 or value == 0 else f"${value:.6f}"


def _budget_gate(store: Store, team: str, config: dict, task_id: str) -> None:
    """Refuse a new attempt once the agreed budget is spent (v2.1 spec 11.2).

    Only new work is refused, and nothing is left behind: the check
    runs before a workspace is made. An attempt that is already running
    is never stopped by the budget.
    """
    # Both sides at the precision of the price table: a budget that is
    # exactly spent is spent, whatever the floating point says.
    budget = config.get("budget_usd")
    if budget is not None:
        spent = _known_spend(store, team)
        if spent >= round(budget, 6):
            raise BudgetExceeded(
                f"BUDGET_LIMIT: {_usd(spent)} of the {_usd(budget)} budget is spent. "
                "New work does not start until the lead raises the budget "
                "(team.py budget --usd <new limit> --lead-token <tok>); "
                "executors already running are not stopped")
    per_task = config.get("budget_per_task_usd")
    if per_task is not None:
        spent = _known_spend(store, team, task_id=task_id)
        if spent >= round(per_task, 6):
            raise BudgetExceeded(
                f"BUDGET_LIMIT: task {task_id} spent its {_usd(per_task)} limit. "
                "No new attempt of this task starts without the lead's decision "
                "(team.py budget --per-task <new limit> --lead-token <tok>)")


KEEP = object()   # a budget value the command did not mention


def op_budget(root: Path, team: str, lead_token: str | None = None, *,
              usd=KEEP, per_task=KEEP, warn_ratio=KEEP) -> dict:
    """Show the budget and where the team stands against it; with the lead
    token, change it (an audited decision, v2.1 spec 11.2).

    A value of ``None`` removes a limit; a number sets it; ``KEEP``
    leaves it as it is.
    """
    from .observability import cost_of

    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "budget"):
            config = merge_config(team_row["config"])
            if any(value is not KEEP for value in (usd, per_task, warn_ratio)):
                _require_lead(team_row, lead_token or "")
                changed = dict(config)
                for key, value in (("budget_usd", usd), ("budget_per_task_usd", per_task),
                                   ("budget_warn_ratio", warn_ratio)):
                    if value is not KEEP:
                        changed[key] = value
                config = merge_config(changed)      # checks the values
                with store.transaction():
                    store.update_team(team, config=config)
                    eventlog.append_event(
                        store.conn, event_type="budget_changed", team_id=team,
                        run_id=(_current_run(store, team) or {}).get("id", ""),
                        actor_id="lead",
                        metadata={"budget_usd": config["budget_usd"],
                                  "budget_per_task_usd": config["budget_per_task_usd"],
                                  "budget_warn_ratio": config["budget_warn_ratio"]})
            spent = _known_spend(store, team)
            unknown = len([record for record in store.list_usage(team)
                           if cost_of(record)[0] is None])
            per_task = config.get("budget_per_task_usd")
            budget, ratio = config["budget_usd"], config["budget_warn_ratio"]
            state = "none"
            if budget is not None:
                state = ("limit" if spent >= round(budget, 6)
                         else "warning" if spent >= round(ratio * budget, 6) else "ok")
            return {"team": team, "budget_usd": budget,
                    "budget_per_task_usd": per_task,
                    "budget_warn_ratio": ratio,
                    "spent_usd": spent,
                    # Where the team stands, decided once, here.
                    "state": state,
                    "remaining_usd": (max(0.0, round(budget - spent, 6))
                                      if budget is not None else None),
                    "unknown_records": unknown,
                    "tasks": {t["id"]: round(_known_spend(store, team, task_id=t["id"]), 6)
                              for t in store.list_tasks(team)
                              if per_task is not None},
                    "tasks_at_limit": [t["id"] for t in store.list_tasks(team)
                                       if per_task is not None
                                       and _known_spend(store, team, task_id=t["id"])
                                       >= round(per_task, 6)]}
    finally:
        store.close()


def _last_event(store: Store, team_id: str) -> dict:
    rows = eventlog.list_events(store.conn, team_id)
    return rows[-1] if rows else {}


# ---------------------------------------------------------------- claim


def op_claim(root: Path, team: str, task_id: str, by: str) -> dict:
    """Claim a pending task for an executor: a new execution attempt.

    Three versions tell the attempt where it stands (spec 3.2, 3.3). The
    trusted base is what the attempt is checked against: the composition
    of the parents' accepted commits, else the run base. The draft
    checkpoint is what the last failed attempt left; the new attempt may
    continue from it, but it never becomes the base. The accepted SHA is
    set by the gate alone.
    """
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, f"claim:{by}"):
            config = merge_config(team_row["config"])
            task = store.get_task(team, task_id)
            if task is None:
                raise NotFoundError(f"task not found: {task_id}")
            # A refused claim leaves nothing behind: who claims, what the
            # task depends on and whether it may be claimed at all are
            # settled before git is touched, so no worktree is made for a
            # claim that fails.
            agent = store.get_agent(team, by)
            if agent and agent["role"] not in ("executor", "lead"):
                raise StateRejected(
                    f"agent {by!r} has role {agent['role']!r}: only executors "
                    "can claim tasks")
            project_root = Path(team_row["project_root"])
            isolated = _isolated(project_root, config)
            dep_base = _dependency_base(store, team, task, inherit=isolated)
            statemachine.assert_transition(
                task["status"], statemachine.TaskStatus.IN_PROGRESS,
                task_id=task_id)
            if _run_is_paused(store, team):
                raise StateRejected(
                    "PAUSED: the lead paused the team; new tasks do not start "
                    "until it is resumed (team.py resume --lead-token <tok>). "
                    "Executors already running are not stopped")
            _budget_gate(store, team, config, task_id)
            # The trusted base the attempt is measured against, and the
            # commit its worktree grows from (spec 3.2, 3.3).
            base = start = None
            if isolated:
                base = _trusted_base(store, project_root, d, team, task,
                                     dep_base, team_row.get("base_commit"),
                                     by=by)
                start = _attempt_start(project_root, task, base)
            workspace = _prepare_workspace(root, d, team, task, config,
                                           base=base, start=start)
            with store.transaction():
                recorded = []
                token = secrets.token_hex(16)
                ts = now_iso()
                lease = (datetime.now(timezone.utc)
                         + timedelta(seconds=config["execution_timeout_s"]))
                statemachine.apply_transition(
                    store.conn, task, statemachine.TaskStatus.IN_PROGRESS,
                    event_type="task_claimed",
                    run_id=_current_run(store, team)["id"], actor_id=by,
                    initiator=statemachine.EXECUTOR, timestamp=ts,
                    metadata={"assignee": task["assignee"]})
                recorded.append(_last_event(store, team))
                fields = {
                    "owner": by, "execution_token_hash": _sha256(token),
                    "attempt": task["attempt"] + 1,
                    "lease_expires_at": lease.isoformat(
                        timespec="milliseconds").replace("+00:00", "Z"),
                    "heartbeat_at": ts}
                if workspace is not None:
                    # Every version of the task is bound to a SHA: the base
                    # the checks are made against, and what it is made of.
                    fields.update(workspace_path=workspace.path,
                                  branch=workspace.branch,
                                  base_commit=base,
                                  trusted_base_commit=base,
                                  dependency_base=dep_base)
                    recorded.append(eventlog.append_event(
                        store.conn, event_type="workspace_created", team_id=team,
                        run_id=_current_run(store, team)["id"], task_id=task_id,
                        actor_id=by,
                        metadata={"branch": workspace.branch, "base": base,
                                  "start": start,
                                  "dependency_base":
                                      [entry["task"] for entry in dep_base]}))
                store.update_task(team, task_id, **fields)
                store.update_agent(team, by, status="running", current_task=task_id,
                                   heartbeat_at=ts)
                recorded.append(eventlog.append_event(
                    store.conn, event_type="agent_started", team_id=team,
                    run_id=_current_run(store, team)["id"], task_id=task_id,
                    actor_id=by, metadata={"attempt": task["attempt"] + 1}))
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
            result = {"task": task_id, "owner": by,
                      "execution_token": token,
                      "attempt": task["attempt"] + 1}
            if workspace is not None:
                result["workspace"] = workspace.path
            return result
    finally:
        store.close()


def _isolated(project_root: Path, config: dict) -> bool:
    """True when every attempt gets a worktree of its own: the team does
    not work 'shared' and the project is a git repository."""
    mode = config.get("workspace", "auto")
    if mode == "shared":
        return False
    return mode == "worktree" or _ws.is_git_project(project_root)


def _dependency_base(store: Store, team: str, task: dict, *,
                     inherit: bool) -> list[dict]:
    """What a task inherits from its parents (spec 3.3): one
    ``{"task", "accepted_commit_sha"}`` per parent, in a deterministic
    order (a parent that depends on another parent follows it).

    Every parent must be completed. Nothing is inherited without
    ``inherit`` (no worktree to grow: shared mode), so the list is empty.
    """
    rows = []
    for dep in dict.fromkeys(task["deps"]):
        row = store.get_task(team, dep)
        if row is None:
            raise NotFoundError(f"dependency not found: {dep}")
        if row["status"] != "completed":
            raise StateRejected(
                f"dependency {dep} is not completed (status={row['status']})")
        rows.append(row)
    if not rows or not inherit:
        return []
    parents = {row["id"] for row in rows}
    order = topological_order(
        [{"id": row["id"], "deps": [dep for dep in row["deps"] if dep in parents]}
         for row in rows]) or [row["id"] for row in rows]
    by_id = {row["id"]: row for row in rows}
    base = []
    for parent in order:
        sha = by_id[parent].get("accepted_commit_sha")
        if not sha:
            raise StateRejected(
                f"dependency {parent} is completed but has no accepted commit, "
                f"so task {task['id']} has nothing to inherit from it")
        base.append({"task": parent, "accepted_commit_sha": sha})
    return base


def _trusted_base(store: Store, project_root: Path, d: Path, team: str,
                  task: dict, dep_base: list[dict], run_base: str | None, *,
                  by: str) -> str | None:
    """The commit the changes of an attempt are measured against (spec
    3.2, 3.3): everything in it is verified.

    In this order: the accepted result of the task itself; for a task
    with parents, the composition it was built on before while that still
    contains every parent result, else a new composition of the parents'
    accepted commits; else the run base. What a failed attempt left is
    never part of it: that is the draft checkpoint, a place to continue
    the work, not a base for the checks.
    """
    accepted = task.get("accepted_commit_sha")
    if accepted:
        return accepted
    sources = [entry["accepted_commit_sha"] for entry in dep_base]
    if not sources:
        return run_base or task.get("base_commit")
    previous = task.get("trusted_base_commit")
    required = ([run_base] if run_base else []) + sources
    if previous and all(_ws.is_ancestor(project_root, sha, previous)
                        for sha in required):
        # The same parents give the same base: a rebuild continues where
        # the last attempt left off.
        return previous
    return _compose_dependency_base(store, project_root, d, team, task,
                                    dep_base, by=by)


def _compose_dependency_base(store: Store, project_root: Path, d: Path,
                             team: str, task: dict, dep_base: list[dict], *,
                             by: str) -> str:
    """Merge the parents' accepted commits into the commit a dependent
    task starts from. StateRejected when a commit is gone or the parents
    conflict; nothing of a failed composition is kept."""
    for entry in dep_base:
        if not _commit_exists(project_root, entry["accepted_commit_sha"]):
            raise StateRejected(
                f"accepted commit {entry['accepted_commit_sha'][:12]} of task "
                f"{entry['task']} is missing from the repository "
                "(garbage collected?)")
    parents = ", ".join(entry["task"] for entry in dep_base)
    composed = _ws.compose_commits(
        project_root, d, task["id"],
        [entry["accepted_commit_sha"] for entry in dep_base],
        message=(f"agent-teams: dependency base of {team}/{task['id']} "
                 f"(parents: {parents})"))
    if isinstance(composed, _ws.CompositionConflict):
        raise _dependency_conflict(store, team, task, dep_base, composed,
                                   by=by)
    return composed


def _commit_exists(project_root: Path, sha: str) -> bool:
    proc = subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                          cwd=str(project_root), capture_output=True, text=True)
    return proc.returncode == 0


def _dependency_conflict(store: Store, team: str, task: dict,
                         dep_base: list[dict], conflict, *,
                         by: str) -> StateRejected:
    """The rejection of a claim whose parents conflict. It names the file
    and both parents, and it is recorded as an event before it is raised
    so that the lead finds it in the logs and in the report."""
    shas = [entry["accepted_commit_sha"] for entry in dep_base]
    incoming = dep_base[shas.index(conflict.incoming)]["task"]
    merged = dep_base[:shas.index(conflict.incoming)]
    files = list(conflict.files)
    owners = [entry["task"] for entry in merged
              if set(files) & _own_changes(store, team, entry)]
    others = owners or [entry["task"] for entry in merged]
    between = [incoming, *others]
    rejected = StateRejected(
        f"task {task['id']}: DEPENDENCY_CONFLICT: the accepted results of "
        f"parents {incoming} and {', '.join(others)} conflict in "
        f"{', '.join(files)}. The dependency base of {task['id']} cannot be "
        "composed: the task stays pending and nothing of the parents was "
        "changed.",
        detail={"code": "DEPENDENCY_CONFLICT", "task": task["id"],
                "files": files, "between": between})
    with store.transaction():
        eventlog.append_event(
            store.conn, event_type="workspace_conflict", team_id=team,
            run_id=_current_run(store, team)["id"], task_id=task["id"],
            actor_id=by, metadata={"kind": "dependency_conflict",
                                   "files": files, "between": between})
        _recorded(store, team, [_last_event(store, team)])
    return rejected


def _own_changes(store: Store, team: str, entry: dict) -> set[str]:
    """The paths the accepted submission of a parent changed against the
    base of its own attempt."""
    for record in store.list_submissions(team, entry["task"]):
        if record.get("commit_sha") == entry["accepted_commit_sha"]:
            return set(record.get("changed_files") or [])
    return set()


def _attempt_start(project_root: Path, task: dict, base: str | None) -> str | None:
    """Where the worktree of an attempt grows from: the draft a failed
    attempt left, when that descends from the trusted base, else the base
    itself. The draft lets the work continue; it does not move the base
    the checks are made against."""
    draft = task.get("draft_checkpoint_commit")
    if base and draft and _ws.is_ancestor(project_root, base, draft):
        return draft
    return base


def _prepare_workspace(root: Path, d: Path, team: str, task: dict,
                       config: dict, *, base: str | None = None,
                       start: str | None = None):
    """Create or reuse the task worktree when the team uses isolation.

    Runs under the state lock but outside the SQLite transaction: git is
    slow and must not hold the database. ``base`` is the trusted base of
    the attempt and ``start`` the commit its worktree grows from (the
    base, or the draft of a failed attempt). An orphaned worktree (a
    claim that stopped after git ran) is reused when it sits on
    ``start``.
    """
    project_root = Path(root).resolve()
    if not _isolated(project_root, config):
        return None
    # Every execution attempt gets a fresh workspace and branch
    # (hardening spec 3.3): a fenced-out old executor physically cannot
    # touch the new attempt's directory.
    return _ws.create_workspace(project_root, d, team, task["id"],
                                start or base, attempt=task["attempt"] + 1)


# ---------------------------------------------------------------- submit


def op_submit(root: Path, team: str, task_id: str, by: str, token: str) -> dict:
    """Submit for verification: clean worktree only, immutable snapshot.

    The accepted-for-verification artifact is the committed HEAD, pinned
    with a git ref and recorded in SQLite. Uncommitted files can no
    longer pass the gate and then vanish from the integration.
    """
    store, d, _team = _open_team(root, team)
    try:
        with StateLock(d, f"submit:{by}"):
            task = store.get_task(team, task_id)
            if task is None:
                raise NotFoundError(f"task not found: {task_id}")
            if task["owner"] != by:
                raise PermissionDenied(
                    f"task {task_id} is owned by {task['owner']!r}, not {by!r}")
            if not token or _sha256(token) != task["execution_token_hash"]:
                raise PermissionDenied(
                    "invalid or stale execution token (the current attempt's "
                    "token is printed once by claim)")

            submission = _build_submission(store, d, team, task)
            with store.transaction():
                recorded = []
                run_id = _current_run(store, team)["id"]
                statemachine.apply_transition(
                    store.conn, task, statemachine.TaskStatus.AWAITING_VERIFICATION,
                    event_type="task_submitted",
                    run_id=run_id, actor_id=by,
                    initiator=statemachine.EXECUTOR, timestamp=now_iso(),
                    metadata={"submission_id": submission["id"]})
                recorded.append(_last_event(store, team))
                store.add_submission({**submission, "run_id": run_id})
                store.update_task(team, task_id,
                                  head_commit=submission["commit_sha"],
                                  metadata={**task.get("metadata", {}),
                                            "changed_files":
                                                submission["changed_files"],
                                            "workspace_violations":
                                                submission["violations"],
                                            "submission_id": submission["id"]})
                if submission["violations"]:
                    recorded.append(eventlog.append_event(
                        store.conn, event_type="workspace_conflict",
                        team_id=team, run_id=run_id, task_id=task_id,
                        actor_id=by,
                        metadata={"violations": submission["violations"]}))
                store.update_agent(team, by, status="waiting_verification",
                                   current_task=task_id)
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
            result = {"task": task_id, "status": "awaiting_verification",
                      "by": by}
            if submission["commit_sha"]:
                result.update(submission_id=submission["id"],
                              commit_sha=submission["commit_sha"],
                              changed_files=len(submission["changed_files"]),
                              write_set_violations=submission["violations"])
            return result
    finally:
        store.close()


def _build_submission(store: Store, d: Path, team: str, task: dict) -> dict:
    """Validate the worktree and build the immutable submission record."""
    from . import workspaces

    ws_path = task.get("workspace_path")
    # The changes of an attempt are cumulative and are checked against
    # the trusted base, never against the draft a failed attempt left
    # (spec 3.2): a violation found in an earlier attempt cannot be hidden
    # by a retry that only adds allowed work.
    base = task.get("trusted_base_commit") or task.get("base_commit")
    common = {"team_id": team, "task_id": task["id"], "attempt": task["attempt"],
              "id": uuid.uuid4().hex}
    if not ws_path or not base or not Path(ws_path).exists():
        # Shared mode: no git, no snapshot; the weaker contract is warned
        # about at init and documented.
        return {**common, "commit_sha": "", "base_commit_sha": None,
                "branch": None, "changed_files": [], "violations": []}

    ws = Path(ws_path)
    project_root = Path(d).parents[1]
    dirty = workspaces.dirty_entries(ws)
    if dirty:
        raise StateRejected(
            "SUBMIT_REJECTED_DIRTY_WORKTREE: commit your work first, then "
            "resubmit. Uncommitted files:\n  "
            + "\n  ".join(dirty[:20])
            + (f"\n  ... and {len(dirty) - 20} more" if len(dirty) > 20 else ""),
            detail={"code": "SUBMIT_REJECTED_DIRTY_WORKTREE", "files": dirty})
    head = workspaces.head_sha(ws)
    if not workspaces.is_ancestor(project_root, base, head):
        raise StateRejected(
            f"SUBMIT_REJECTED_DETACHED_HEAD: HEAD {head[:12]} is not a "
            f"descendant of the task base {base[:12]}; rebase or redo the "
            "work on top of the task base",
            detail={"code": "SUBMIT_REJECTED_DETACHED_HEAD", "head": head,
                    "base": base})
    # The results of the parents are the inputs of the task: a copy of
    # one of them is not a write outside files_write (spec 3.3).
    inherited = _inherited_paths(project_root, store, team, task, base)
    diff = workspaces.capture_diff(ws, base)
    violations = workspaces.write_set_violations(diff, task["files_write"],
                                                 inherited)
    workspaces.pin_submission(project_root, team, task["id"], common["id"],
                              head)
    return {**common, "commit_sha": head, "base_commit_sha": base,
            "branch": task.get("branch"),
            "changed_files": list(workspaces.judged_changes(diff, inherited)),
            "violations": violations}


# ---------------------------------------------------------------- verify


def _inherited_paths(project_root: Path, store: Store, team: str,
                     task: dict, base: str | None) -> tuple[str, ...]:
    """The paths a task with parents received from them (spec 3.3): what
    its trusted base holds on top of the run base."""
    if not base or not task.get("dependency_base"):
        return ()
    run_base = (store.get_team(team) or {}).get("base_commit")
    if not run_base or run_base == base:
        return ()
    return _ws.changed_paths(project_root, run_base, base)


def _evidence_notes(report: dict) -> list[dict]:
    """The citations of a validated report, one note per evidence item:
    what was checked to exist in the snapshot of the submission."""
    return [{"criterion_id": criterion.get("criterion_id"),
             "path": item.get("path"),
             "line_start": item.get("line_start"),
             "line_end": item.get("line_end")}
            for criterion in report.get("criteria", [])
            for item in criterion.get("evidence") or []]


def op_verify(root: Path, team: str, task_id: str, verdict: str,
              feedback: str, lead_token: str, *, by: str = "verifier",
              report: dict | None = None,
              override_reason: str = "") -> dict:
    if verdict not in ("pass", "fail"):
        raise UsageError("verdict must be pass or fail")
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, f"verify:{by}"):
            _require_lead(team_row, lead_token)
            with store.transaction():
                recorded = []
                task = store.get_task(team, task_id)
                if task is None:
                    raise NotFoundError(f"task not found: {task_id}")
                if task["status"] != "awaiting_verification":
                    raise StateRejected(
                        f"task {task_id} is not awaiting verification "
                        f"(status={task['status']}); the executor must submit first")
                config = merge_config(team_row["config"])
                run_id = _current_run(store, team)["id"]
                attempt_no = len(store.list_attempts(team, task_id)) + 1
                if verdict == "fail" and not feedback.strip():
                    raise UsageError("verdict fail requires --feedback")

                # Machine gate (spec 7.2): required command-backed criteria
                # and write-set violations block a PASS no matter who says
                # otherwise; the verdict is overridden to FAIL. Checks run
                # against the immutable submission snapshot, never against
                # the executor's live worktree (hardening spec 2.3).
                from . import quality, workspaces

                submission = store.latest_submission(team, task_id)
                snapshot_task = task
                snapshot_dir = None
                project_root = Path(team_row["project_root"])
                if submission and submission.get("commit_sha"):
                    snapshot_dir = workspaces.checkout_snapshot(
                        project_root, d, task_id, submission["commit_sha"])
                    snapshot_task = {**task,
                                     "workspace_path": str(snapshot_dir)}
                # What the evidence of a PASS was checked against: the
                # snapshot of the submission ("verified"), or nothing
                # ("not_checked": a lead override, or no snapshot to check
                # against). It stays "pending" while the gate says FAIL.
                evidence_status, evidence_notes = "pending", []
                try:
                    forced, criteria_report = quality.forced_failures(
                        snapshot_task, project_root)
                    # Verification enforcement (hardening spec 4.2): a PASS
                    # needs a structured report bound to this submission, or
                    # an explicit lead override with a reason (audited).
                    override = False
                    if verdict == "pass":
                        if submission is None or not submission.get("commit_sha"):
                            # Shared mode: no snapshot to bind a report to, so
                            # a PASS needs none. A reason given is still the
                            # lead's own decision, and is recorded as one:
                            # the board must never show it as a passed check.
                            override = bool(override_reason.strip())
                        elif report is None:
                            if override_reason.strip():
                                override = True
                            else:
                                raise UsageError(
                                    "VERIFICATION_REPORT_MISSING: a PASS "
                                    "requires --report (the verifier's JSON) or "
                                    "--override-reason with an explicit lead "
                                    "decision",
                                    detail={"code": "VERIFICATION_REPORT_MISSING"})
                        else:
                            from .verification import validate_report

                            # The evidence is checked where it has to exist:
                            # in the immutable snapshot of the submission,
                            # never in the executor's live worktree (spec 3.4).
                            validate_report(report, task=task,
                                            submission=submission,
                                            snapshot_root=snapshot_dir)
                            evidence_status = ("verified"
                                               if snapshot_dir is not None
                                               else "not_checked")
                            evidence_notes = _evidence_notes(report)
                finally:
                    # The snapshot lives exactly as long as the checks.
                    if snapshot_dir is not None:
                        workspaces.drop_snapshot(project_root, d, task_id)

                if verdict == "pass" and forced:
                    verdict = "fail"
                    feedback = ("Quality Gate override: " + "; ".join(forced))
                report = dict(report or {})
                report.setdefault("criteria", criteria_report)
                report.setdefault("forced_failures", forced)

                if verdict == "pass" and submission is not None:
                    store.update_submission(
                        submission["id"], verification_status="passed",
                        verifier=by,
                        report=report or {"override_reason": override_reason}
                        if override else (report or {}),
                        evidence_status=evidence_status,
                        evidence_notes=evidence_notes)
                if override and submission is not None:
                    store.update_submission(
                        submission["id"], verification_status="overridden",
                        verifier=by, evidence_status="not_checked")

                if verdict == "pass":
                    store.add_attempt({
                        "id": uuid.uuid4().hex, "team_id": team, "run_id": run_id,
                        "task_id": task_id, "attempt": attempt_no, "verifier": by,
                        "actor": "lead", "verdict": "pass",
                        "report": report or {}, "feedback": ""})
                    if override:
                        recorded.append(eventlog.append_event(
                            store.conn, event_type="verification_passed",
                            team_id=team, run_id=run_id, task_id=task_id,
                            actor_id="lead",
                            metadata={"override": True,
                                      "override_reason": override_reason,
                                      "submission_id":
                                          (submission or {}).get("id")}))
                    accepted_sha = (submission or {}).get("commit_sha") or None
                    if accepted_sha:
                        # From now on the accepted result is the trusted
                        # base of the task; what its attempt grew out of
                        # is no longer a draft.
                        store.update_task(team, task_id,
                                          accepted_commit_sha=accepted_sha,
                                          trusted_base_commit=accepted_sha,
                                          draft_checkpoint_commit=None)
                    statemachine.apply_transition(
                        store.conn, task, statemachine.TaskStatus.COMPLETED,
                        event_type="verification_passed", run_id=run_id,
                        actor_id=by, initiator=statemachine.GATE,
                        timestamp=now_iso(),
                        metadata={"attempt": attempt_no,
                                  "accepted_commit_sha": accepted_sha,
                                  "submission_id":
                                      (submission or {}).get("id")})
                    recorded.append(_last_event(store, team))
                    recorded.append(eventlog.append_event(
                        store.conn, event_type="task_completed", team_id=team,
                        run_id=run_id, task_id=task_id, actor_id="lead",
                        metadata={"verdict": "pass"}))
                    store.update_agent(team, task["owner"], status="idle",
                                       current_task=None, finished_at=now_iso())
                    outcome = {"task": task_id, "status": "completed",
                               "failed_rounds": task["verification_failures"]}
                else:
                    if submission is not None:
                        store.update_submission(
                            submission["id"], verification_status="failed",
                            verifier=by)
                    failures = task["verification_failures"] + 1
                    store.add_attempt({
                        "id": uuid.uuid4().hex, "team_id": team, "run_id": run_id,
                        "task_id": task_id, "attempt": attempt_no, "verifier": by,
                        "actor": "lead", "verdict": "fail",
                        "report": report or {}, "feedback": feedback})
                    draft = {}
                    if submission is not None and submission.get("commit_sha"):
                        # What the attempt left is kept as a draft: the
                        # next attempt may continue from it, but it is
                        # never a trusted base (spec 3.2).
                        draft["draft_checkpoint_commit"] = submission["commit_sha"]
                    store.update_task(team, task_id,
                                      verification_failures=failures, **draft)
                    extra = {"attempt": attempt_no, "failures": failures,
                             "feedback": feedback}
                    if failures >= config["max_verification_failures"]:
                        reason = (f"{failures} failed verification rounds "
                                  f"(limit {config['max_verification_failures']})")
                        statemachine.apply_transition(
                            store.conn, task, statemachine.TaskStatus.BLOCKED,
                            event_type="task_blocked", run_id=run_id,
                            actor_id=by, initiator=statemachine.GATE,
                            timestamp=now_iso(), reason=reason, metadata=extra)
                        recorded.append(_last_event(store, team))
                        _mailbox(store, team, run_id, task["owner"], "gate",
                                 task_id, "round_limit",
                                 f"ROUND LIMIT on task {task_id}: {reason}. "
                                 "The task goes to the lead (release or "
                                 "handle manually)")
                        outcome = {"task": task_id, "status": "blocked",
                                   "failed_rounds": failures}
                    else:
                        # Back to the same execution attempt: claim data and
                        # the execution token stay valid.
                        statemachine.apply_transition(
                            store.conn, task, statemachine.TaskStatus.IN_PROGRESS,
                            event_type="verification_failed", run_id=run_id,
                            actor_id=by, initiator=statemachine.GATE,
                            timestamp=now_iso(), metadata=extra)
                        recorded.append(_last_event(store, team))
                        _mailbox(store, team, run_id, task["owner"], "gate",
                                 task_id, "verify_fail",
                                 f"VERIFY FAIL on task {task_id} (round "
                                 f"{failures}): {feedback}")
                        outcome = {"task": task_id, "status": "in_progress",
                                   "failed_rounds": failures}
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
        return outcome
    finally:
        store.close()


# ---------------------------------------------------------------- lead ops


def op_release(root: Path, team: str, task_id: str, lead_token: str,
               reason: str = "") -> dict:
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "release:lead"):
            _require_lead(team_row, lead_token)
            with store.transaction():
                recorded = []
                task = store.get_task(team, task_id)
                if task is None:
                    raise NotFoundError(f"task not found: {task_id}")
                config = merge_config(team_row["config"])
                retries = task["execution_retries"] + 1
                if retries > config["max_execution_retries"]:
                    raise StateRejected(
                        f"task {task_id} exhausted its execution retries "
                        f"({task['execution_retries']} used, limit "
                        f"{config['max_execution_retries']}): start a new task")
                statemachine.apply_transition(
                    store.conn, task, statemachine.TaskStatus.PENDING,
                    event_type="task_released",
                    run_id=_current_run(store, team)["id"], actor_id="lead",
                    initiator=statemachine.LEAD, timestamp=now_iso(),
                    reason=reason or "released by the lead",
                    metadata={"execution_retries": retries})
                recorded.append(_last_event(store, team))
                # A reopened task starts a fresh verification cycle; the
                # history stays in verification_attempts and the event log.
                store.update_task(team, task_id, execution_retries=retries,
                                  verification_failures=0)
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
            return {"task": task_id, "status": "pending",
                    "execution_retries": retries}
    finally:
        store.close()


def op_cancel(root: Path, team: str, task_id: str, lead_token: str,
              reason: str = "") -> dict:
    """Cancel a task (v2.1 spec 17: the owner asks to drop a stage).

    A pending task is closed as it is. A task that is in work is closed
    too, since 2.1.0a5: its attempt is fenced the way recovery fences a
    lost executor. The execution token dies with the transition, the
    workspace moves to quarantine (nothing of it is lost, nothing of it
    is trusted) and the specialist is free for other work.
    """
    from . import workspaces

    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "cancel:lead"):
            _require_lead(team_row, lead_token)
            with store.transaction():
                task = store.get_task(team, task_id)
                if task is None:
                    raise NotFoundError(f"task not found: {task_id}")
                in_work = task["status"] in ("in_progress", "awaiting_verification")
                statemachine.apply_transition(
                    store.conn, task, statemachine.TaskStatus.CANCELLED,
                    event_type="task_cancelled",
                    run_id=_current_run(store, team)["id"], actor_id="lead",
                    initiator=statemachine.LEAD, timestamp=now_iso(),
                    reason=reason or "cancelled by the lead")
                recorded = [_last_event(store, team)]
                if in_work:
                    # The attempt is dead: its token can submit nothing any more.
                    store.update_task(team, task_id, execution_token_hash=None)
                    agent = store.get_agent(team, task.get("owner") or "")
                    if agent and agent.get("current_task") == task_id:
                        store.update_agent(team, agent["id"], status="idle",
                                           current_task=None)
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
            fenced = None
            if in_work and task.get("workspace_path"):
                # Outside the transaction: a filesystem move must not sit
                # inside the SQLite critical section.
                fenced = workspaces.quarantine_attempt(d, task_id, task["attempt"])
                if fenced is not None:
                    with store.transaction():
                        store.update_task(
                            team, task_id, workspace_path=None, branch=None,
                            metadata={**task.get("metadata", {}),
                                      "quarantined_at": now_iso(),
                                      "quarantine_dir": str(fenced)})
            return {"task": task_id, "status": "cancelled", "fenced": in_work,
                    "quarantine": str(fenced) if fenced else None}
    finally:
        store.close()


# ------------------------------------------------------- pause and the board's actions

def _run_is_paused(store: Store, team: str) -> str | None:
    """When the lead paused the team, the moment it happened; else None.

    The pause lives in the run's own record: it is a fact of the run,
    not a setting, and it needs no column of its own.
    """
    run = _current_run(store, team) or {}
    paused = (run.get("metadata") or {}).get("paused_at")
    return paused if isinstance(paused, str) and paused else None


def op_pause(root: Path, team: str, lead_token: str, reason: str = "") -> dict:
    """Stop new tasks from starting (v2.1 spec 17).

    An attempt that is already running is never stopped, exactly as with
    the budget: only new work waits. Idempotent: pausing a paused team
    changes nothing and writes nothing.
    """
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "pause:lead"):
            _require_lead(team_row, lead_token)
            with store.transaction():
                run = _current_run(store, team)
                if run is None or run["status"] != "active":
                    raise StateRejected(
                        f"team {team} is not in work (run status "
                        f"{(run or {}).get('status')}); there is nothing to pause")
                if _run_is_paused(store, team):
                    return {"team": team, "paused": True, "already": True}
                paused_at = now_iso()
                store.update_run(run["id"],
                                 metadata={**(run.get("metadata") or {}),
                                           "paused_at": paused_at})
                eventlog.append_event(
                    store.conn, event_type="run_paused", team_id=team,
                    run_id=run["id"], actor_id="lead",
                    metadata={"reason": reason or "paused by the lead"})
                _recorded(store, team, [_last_event(store, team)])
                _refresh_mirrors(store, team)
            return {"team": team, "paused": True, "already": False,
                    "paused_at": paused_at}
    finally:
        store.close()


def op_resume(root: Path, team: str, lead_token: str) -> dict:
    """Let new tasks start again after a pause (v2.1 spec 17)."""
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "resume:lead"):
            _require_lead(team_row, lead_token)
            with store.transaction():
                run = _current_run(store, team)
                if run is None or not _run_is_paused(store, team):
                    return {"team": team, "paused": False, "already": True,
                            "paused_at": None}
                metadata = dict(run.get("metadata") or {})
                paused_at = metadata.pop("paused_at", None)
                store.update_run(run["id"], metadata=metadata)
                eventlog.append_event(
                    store.conn, event_type="run_resumed", team_id=team,
                    run_id=run["id"], actor_id="lead",
                    metadata={"paused_at": paused_at})
                _recorded(store, team, [_last_event(store, team)])
                _refresh_mirrors(store, team)
            return {"team": team, "paused": False, "already": False,
                    "paused_at": paused_at}
    finally:
        store.close()


# What the owner can ask from the board (v2.1 spec 17), and which of
# these ask for a confirmation first: their consequences are not undone
# from the board.
ACTIONS = ("pause", "resume", "retry", "cancel", "request_check",
           "integrate", "apply", "budget")
DANGEROUS_ACTIONS = frozenset({"pause", "cancel", "integrate", "apply", "budget"})


def op_action(root: Path, team: str, action: str, lead_token: str, *,
              task_id: str | None = None, usd: float | None = None,
              reason: str = "", confirm: bool = False) -> dict:
    """One door for every control action of the board (v2.1 spec 17).

    The board never edits state itself and never holds the lead token on
    the page: the server keeps it in memory and calls this. The action
    is checked here and done by the operation that owns it, under the
    state lock, with the lead's authority. ``confirm`` must be True for
    an action with consequences the owner has to agree to first.
    """
    if action not in ACTIONS:
        raise UsageError(
            f"unknown action {action!r} (known: {', '.join(ACTIONS)})")
    if action in DANGEROUS_ACTIONS and not confirm:
        raise UsageError(f"action {action!r} needs a confirmation")
    if action == "pause":
        result = op_pause(root, team, lead_token, reason)
    elif action == "resume":
        result = op_resume(root, team, lead_token)
    elif action == "retry":
        if not task_id:
            raise UsageError("action 'retry' needs a task")
        result = op_release(root, team, task_id, lead_token, reason)
    elif action == "cancel":
        if not task_id:
            raise UsageError("action 'cancel' needs a task")
        result = op_cancel(root, team, task_id, lead_token, reason)
    elif action == "request_check":
        if not task_id:
            raise UsageError("action 'request_check' needs a task")
        # The board runs no agent: it asks the lead through the mailbox,
        # the way everything else reaches the lead.
        result = op_message(root, team, "lead", "owner",
                            f"OWNER REQUEST: please verify task {task_id} "
                            "(asked for from the board)")
    elif action == "integrate":
        result = op_integrate(root, team, lead_token)
    elif action == "apply":
        result = op_apply(root, team, lead_token)
    else:
        if usd is None:
            raise UsageError("action 'budget' needs usd")
        result = op_budget(root, team, lead_token, usd=usd)
    return {"action": action, **result}


def op_recover(root: Path, team: str, lead_token: str,
               task_id: str | None = None) -> dict:
    """Recovery (hardening spec 3): requeue tasks of lost executors.

    A task is stale when ANY of these holds:
    - the lease expired (no heartbeat within execution_timeout_s),
    - heartbeat_timeout_s passed since the last heartbeat,
    - the whole attempt exceeded execution_timeout_s (heartbeats cannot
      extend the attempt forever).
    The old attempt is fenced: its execution token dies with the state
    transition and its workspace moves to quarantine for manual salvage.
    Idempotent: a repeated call on an already-recovered task is a no-op.
    """
    from . import workspaces

    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "recover:lead"):
            _require_lead(team_row, lead_token)
            config = merge_config(team_row["config"])
            now = datetime.now(timezone.utc)
            recovered: list[dict] = []
            with store.transaction():
                candidates = ([store.get_task(team, task_id)] if task_id
                              else store.list_tasks(team, status="in_progress"))
                for task in candidates:
                    if task is None:
                        raise NotFoundError(f"task not found: {task_id}")
                    reason = _staleness(task, config, now)
                    if reason is None:
                        continue
                    restarts = task["restarts"] + 1
                    run_id = _current_run(store, team)["id"]
                    if restarts > config["max_agent_restarts"]:
                        statemachine.apply_transition(
                            store.conn, task, statemachine.TaskStatus.BLOCKED,
                            event_type="task_blocked", run_id=run_id,
                            actor_id="recovery", initiator=statemachine.RECOVERY,
                            timestamp=now_iso(),
                            reason="agent restart limit reached after timeouts")
                        _mailbox(store, team, run_id, task["owner"], "recovery",
                                 task["id"], "round_limit",
                                 f"RESTART LIMIT on task {task['id']}: the "
                                 "executor timed out too many times, the task "
                                 "goes to the lead")
                        recovered.append({"task": task["id"], "action": "blocked"})
                        continue
                    recorded = [eventlog.append_event(
                        store.conn, event_type="agent_timeout", team_id=team,
                        run_id=run_id, task_id=task["id"],
                        actor_id=task["owner"] or "unknown",
                        metadata={"restarts": restarts, "reason": reason})]
                    statemachine.apply_transition(
                        store.conn, task, statemachine.TaskStatus.BLOCKED,
                        event_type="task_blocked", run_id=run_id,
                        actor_id="recovery", initiator=statemachine.RECOVERY,
                        timestamp=now_iso(),
                        reason=f"executor lost: {reason}")
                    recorded.append(_last_event(store, team))
                    statemachine.apply_transition(
                        store.conn, {**task, "status": "blocked"},
                        statemachine.TaskStatus.PENDING,
                        event_type="task_released", run_id=run_id,
                        actor_id="recovery", initiator=statemachine.RECOVERY,
                        timestamp=now_iso(),
                        reason="executor lost, back to queue")
                    recorded.append(_last_event(store, team))
                    store.update_task(team, task["id"], restarts=restarts)
                    _recorded(store, team, recorded)
                    recovered.append({"task": task["id"], "action": "requeued",
                                      "reason": reason, "restarts": restarts})
            # Quarantine outside the transaction: filesystem moves must not
            # sit inside the SQLite critical section.
            for entry in recovered:
                if entry["action"] != "requeued":
                    continue
                task = store.get_task(team, entry["task"])
                if task and task.get("workspace_path"):
                    source = Path(task["workspace_path"])
                    attempt_no = task["attempt"]
                    quarantined = workspaces.quarantine_attempt(
                        d, entry["task"], attempt_no)
                    if quarantined is not None:
                        with store.transaction():
                            store.update_task(
                                team, entry["task"], workspace_path=None,
                                branch=None,
                                metadata={**task.get("metadata", {}),
                                          "quarantined_at": now_iso(),
                                          "quarantine_dir": str(quarantined)})
                            eventlog.append_event(
                                store.conn, event_type="agent_timeout",
                                team_id=team,
                                run_id=_current_run(store, team)["id"],
                                task_id=entry["task"], actor_id="recovery",
                                metadata={"quarantine": str(quarantined),
                                          "attempt": attempt_no})
                entry["quarantined"] = str(entry.get("quarantine", "") or "")
            _refresh_mirrors(store, team)
            return {"recovered": recovered}
    finally:
        store.close()


def _staleness(task: dict, config: dict, now: datetime) -> str | None:
    """Why a task counts as lost; None when the executor looks alive."""
    lease = task.get("lease_expires_at")
    if lease and _parse_iso(lease) < now:
        return "lease expired"
    beat = task.get("heartbeat_at")
    if beat and _parse_iso(beat) < now - timedelta(
            seconds=config["heartbeat_timeout_s"]):
        return "heartbeat timeout"
    started = task.get("claimed_at")
    if started and _parse_iso(started) < now - timedelta(
            seconds=config["execution_timeout_s"]):
        return "execution timeout"
    return None


def op_heartbeat(root: Path, team: str, by: str, token: str) -> dict:
    store, d, _team = _open_team(root, team)
    try:
        with StateLock(d, f"heartbeat:{by}"), store.transaction():
            agent = store.get_agent(team, by)
            if agent is None or not agent.get("current_task"):
                raise NotFoundError(f"agent {by!r} has no task in progress")
            task = store.get_task(team, agent["current_task"])
            if task is None or task["owner"] != by:
                raise StateRejected(f"agent {by!r} does not own a task now")
            if not token or _sha256(token) != task["execution_token_hash"]:
                raise PermissionDenied("invalid or stale execution token")
            config = merge_config(_team["config"])
            ts = now_iso()
            lease = (datetime.now(timezone.utc)
                     + timedelta(seconds=config["execution_timeout_s"]))
            store.update_task(team, task["id"], heartbeat_at=ts,
                              lease_expires_at=lease.isoformat(
                                  timespec="milliseconds").replace("+00:00", "Z"))
            store.update_agent(team, by, heartbeat_at=ts)
            return {"agent": by, "task": task["id"], "heartbeat_at": ts}
    finally:
        store.close()



# ---------------------------------------------------------------- tokens / mailbox


def op_tokens(root: Path, team: str, task_id: str, add: int, by: str = "lead",
              model: str | None = None, source: str = "manual",
              dedup_key: str | None = None) -> dict:
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, f"tokens:{by}"), store.transaction():
            task = store.get_task(team, task_id)
            if task is None:
                raise NotFoundError(f"task not found: {task_id}")
            run = _current_run(store, team)
            if add:
                from .observability import compute_cost

                # The cost is stored with the record, as for a notification:
                # it is the price of the day the work was done.
                cost, cost_quality = compute_cost({"model": model, "total_tokens": add})
                store.add_usage({
                    "team_id": team, "run_id": run["id"] if run else "",
                    "task_id": task_id, "agent_id": by, "model": model,
                    "total_tokens": add, "cost_usd": cost,
                    "cost_quality": cost_quality, "source": source,
                    "dedup_key": dedup_key})
                store.update_task(team, task_id, tokens=task["tokens"] + add)
                eventlog.append_event(
                    store.conn, event_type="token_usage_recorded", team_id=team,
                    run_id=run["id"] if run else "", task_id=task_id,
                    actor_id=by, metadata={"add": add,
                                           "total": task["tokens"] + add})
                _recorded(store, team, [_last_event(store, team)])
                _refresh_mirrors(store, team)
            return {"task": task_id, "tokens": task["tokens"] + (add or 0)}
    finally:
        store.close()


def op_message(root: Path, team: str, to: str, by: str, text: str) -> dict:
    for name in (to, by):
        if not NAME_RE.match(name or ""):
            raise UsageError(f"invalid agent name: {name!r}")
    store, d, _team = _open_team(root, team)
    try:
        with StateLock(d, f"message:{by}"), store.transaction():
            run = _current_run(store, team)
            row = _mailbox(store, team, run["id"] if run else "", to, by,
                           None, "message", text)
            eventlog.append_event(store.conn, event_type="message_sent",
                                  team_id=team, run_id=run["id"] if run else "",
                                  actor_id=by, metadata={"to": to})
            _recorded(store, team, [_last_event(store, team)])
            _refresh_mirrors(store, team)
            return {"id": row["id"], "to": to, "from": by}
    finally:
        store.close()


def op_read(root: Path, team: str, agent: str) -> list[dict]:
    if not NAME_RE.match(agent or ""):
        raise UsageError(f"invalid agent name: {agent!r}")
    store, d, _team = _open_team(root, team)
    try:
        with StateLock(d, f"read:{agent}"), store.transaction():
            unread = store.list_messages(team, to_agent=agent, unread_only=True)
            store.mark_read(team, [m["id"] for m in unread], now_iso())
            _refresh_mirrors(store, team)
            return [{"id": m["id"], "from": m["from_agent"], "to": m["to_agent"],
                     "text": m["text"], "ts": m["created_at"],
                     "task": m["task_id"], "kind": m["kind"]} for m in unread]
    finally:
        store.close()


# ---------------------------------------------------------------- integrate


def _integration_order(store: Store, team: str) -> list[dict]:
    """Accepted tasks in topological order of their dependencies."""
    from .validation import topological_order

    tasks = store.list_tasks(team)
    order = topological_order(tasks) or sorted(t["id"] for t in tasks)
    by_id = {t["id"]: t for t in tasks}
    return [by_id[tid] for tid in order]


def op_integrate(root: Path, team: str, lead_token: str,
                 check_command: str = "", *, apply: bool = False) -> dict:
    """Integration gate (hardening spec 5): merge the ACCEPTED commit SHAs
    in DAG order into a fresh integration branch, run the check command
    there. Branches are mutable; only the pinned SHA that passed
    verification may be integrated. Apply is a separate command.
    """
    from . import quality, workspaces

    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "integrate:lead"):
            _require_lead(team_row, lead_token)
            tasks = store.list_tasks(team)
            # A stage the owner cancelled is not in the way of the rest
            # (v2.1 spec 17): what was accepted is integrated.
            unfinished = [t["id"] for t in tasks
                          if t["status"] not in ("completed", "cancelled")]
            if unfinished:
                raise StateRejected(
                    "cannot integrate: tasks not completed: "
                    + ", ".join(unfinished))
            run = _current_run(store, team)
            run_id = run["id"] if run else ""
            git_tasks = [t for t in tasks if t.get("accepted_commit_sha")]
            if not git_tasks:
                with store.transaction():
                    store.update_run(run_id, status="finished",
                                     finished_at=now_iso())
                    recorded = [eventlog.append_event(
                        store.conn, event_type="team_completed", team_id=team,
                        run_id=run_id, actor_id="lead",
                        metadata={"mode": "shared"})]
                    _recorded(store, team, recorded)
                result = {"ok": True, "mode": "shared", "applied": False}
                if apply:
                    result["applied"] = False
                return result

            project_root = Path(team_row["project_root"])
            if not workspaces.is_git_project(project_root):
                raise StateRejected(
                    "accepted snapshots exist but the project is not a git "
                    "repository anymore")
            ordered = _integration_order(store, team)
            shas = [{"task": t["id"], "sha": t["accepted_commit_sha"]}
                    for t in ordered if t.get("accepted_commit_sha")]
            for entry in shas:
                ref = workspaces.submission_ref(team, entry["task"], "*")
                # The ref itself is not needed for the merge (the SHA is
                # pinned), but the commit must still exist.
                probe = subprocess.run(
                    ["git", "cat-file", "-e", entry["sha"] + "^{commit}"],
                    cwd=str(project_root), capture_output=True, text=True)
                if probe.returncode != 0:
                    raise StateRejected(
                        f"accepted commit {entry['sha'][:12]} of task "
                        f"{entry['task']} is missing from the repository "
                        "(garbage collected?)")

            with store.transaction():
                store.update_run(run_id, status="integration_running")
            result = quality.merge_shas(
                project_root, d, team,
                [entry["sha"] for entry in shas],
                check_command=check_command)
            if not result["ok"]:
                with store.transaction():
                    recorded = [eventlog.append_event(
                        store.conn, event_type="workspace_conflict",
                        team_id=team, run_id=run_id, actor_id="lead",
                        metadata={"conflicts": result["conflicts"],
                                  "check": result["check"]})]
                    _recorded(store, team, recorded)
                    store.update_run(run_id, status="integration_failed")
                quality.cleanup_integration(project_root, d, team,
                                            keep_branch=False)
                return {"ok": False, "mode": "worktree", **result,
                        "applied": False}
            integration_sha = workspaces.head_sha(Path(result["workspace"]))
            integration_base = quality.integration_base_of(project_root, team)
            with store.transaction():
                recorded = [eventlog.append_event(
                    store.conn, event_type="workspace_integrated",
                    team_id=team, run_id=run_id, actor_id="lead",
                    metadata={"merged": shas, "check": result["check"],
                              "integration_commit_sha": integration_sha})]
                store.update_run(run_id, status="apply_pending",
                                 integration_base_commit=integration_base,
                                 integration_commit_sha=integration_sha,
                                 finished_at=None)
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
            if apply:
                applied = _apply_locked(store, d, team_row, lead_token,
                                         team=team)
                result["applied"] = applied.get("applied", False)
                result["apply_error"] = applied.get("error")
            else:
                result["applied"] = False
            return {"ok": True, "mode": "worktree",
                    "merged": shas, "check": result["check"],
                    "workspace": result["workspace"],
                    "integration_commit_sha": integration_sha,
                    "applied": result["applied"]}
    finally:
        store.close()


def op_apply(root: Path, team: str, lead_token: str) -> dict:
    """Apply the validated integration branch to the project branch."""
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "apply:lead"):
            return _apply_locked(store, d, team_row, lead_token)
    finally:
        store.close()


def _apply_locked(store: Store, d: Path, team_row: dict,
                  lead_token: str, team: str | None = None) -> dict:
    """Apply core; the caller holds the state lock (hardening spec 5.3).

    Fast-forward only: if the project moved or the worktree is dirty, the
    run stays apply_pending and the lead re-integrates against the new
    base. A failed apply never finishes the run.
    """
    import subprocess as _sp

    from . import workspaces

    team = team or team_row["id"]
    _require_lead(team_row, lead_token)
    run = _current_run(store, team)
    if not run or run["status"] not in ("apply_pending",
                                        "integration_validated"):
        raise StateRejected(
            f"nothing to apply: run status is "
            f"{run['status'] if run else 'none'}; run integrate first")
    project_root = Path(team_row["project_root"])
    dirty = workspaces.dirty_entries(project_root)
    if dirty:
        raise StateRejected(
            "APPLY_REJECTED_DIRTY_PROJECT: the project worktree has "
            "uncommitted changes:\n  " + "\n  ".join(dirty[:20]),
            detail={"code": "APPLY_REJECTED_DIRTY_PROJECT",
                    "files": dirty})
    current = workspaces.head_sha(project_root)
    expected = run.get("integration_base_commit")
    if expected and current != expected:
        raise StateRejected(
            "APPLY_REJECTED_BASE_MOVED: the project branch changed "
            f"after integration ({expected[:12]} -> {current[:12]}); "
            "run integrate again against the new base",
            detail={"code": "APPLY_REJECTED_BASE_MOVED",
                    "expected": expected, "current": current})
    branch = f"agent-teams/{team_row['id']}/integration"
    proc = _sp.run(["git", "merge", "--ff-only", branch],
                   cwd=str(project_root), capture_output=True,
                   text=True, timeout=180)
    if proc.returncode != 0:
        raise StateRejected(
            "apply failed (fast-forward merge refused): "
            + proc.stderr.strip()[:200])
    run_id = run["id"]
    with store.transaction():
        recorded = [eventlog.append_event(
            store.conn, event_type="workspace_integrated",
            team_id=team_row["id"], run_id=run_id, actor_id="lead",
            metadata={"applied": True, "applied_at": now_iso()})]
        store.update_run(run_id, status="finished", finished_at=now_iso())
        recorded.append(eventlog.append_event(
            store.conn, event_type="team_completed", team_id=team_row["id"],
            run_id=run_id, actor_id="lead", metadata={"applied": True}))
        _recorded(store, team, recorded)
        _refresh_mirrors(store, team_row["id"])
    return {"applied": True, "run": "finished"}


# ---------------------------------------------------------------- metrics


def op_metrics(root: Path, team: str) -> dict:
    from .observability import collect_metrics

    store, d, _team = _open_team(root, team)
    try:
        run = _current_run(store, team)
        return collect_metrics(store, team, run_id=run["id"] if run else None)
    finally:
        store.close()


def op_usage(root: Path, team: str, task_id: str, agent_id: str,
             notification_path: Path) -> dict:
    """Record token usage from a subagent notification file (spec 9.1)."""
    import hashlib

    from .observability import parse_notification, record_usage

    text = Path(notification_path).read_text(encoding="utf-8")
    record = parse_notification(text)
    if record is None:
        raise UsageError(
            "no <usage> block found in the notification: nothing was "
            "recorded (the runtime did not provide the numbers)")
    record["dedup_key"] = hashlib.sha1(
        f"{team}|{task_id}|{agent_id}|{text[:400]}".encode()).hexdigest()
    store, d, _team = _open_team(root, team)
    try:
        with StateLock(d, f"usage:{agent_id}"):
            task = store.get_task(team, task_id)
            if task is None:
                raise NotFoundError(f"task not found: {task_id}")
            run = _current_run(store, team)
            with store.transaction():
                row_id = record_usage(store, team, run["id"] if run else "",
                                      task_id, agent_id, record)
                if row_id is None:
                    return {"recorded": False, "reason": "duplicate"}
                store.update_task(team, task_id,
                                  tokens=task["tokens"]
                                  + (record.get("total_tokens") or 0))
                eventlog.append_event(
                    store.conn, event_type="token_usage_recorded",
                    team_id=team, run_id=run["id"] if run else "",
                    task_id=task_id, actor_id=agent_id,
                    metadata={"add": record.get("total_tokens"),
                              "source": "notification"})
            _refresh_mirrors(store, team)
            return {"recorded": True,
                    "total_tokens": record.get("total_tokens"),
                    "tool_uses": record.get("tool_uses"),
                    "duration_ms": record.get("duration_ms")}
    finally:
        store.close()


# ---------------------------------------------------------------- inspection extras


def op_graph(root: Path, team: str) -> dict:
    """ASCII DAG of the current statuses."""
    store, d, _team = _open_team(root, team)
    try:
        tasks = store.list_tasks(team)
        lines = []
        for task in tasks:
            prefix = " -> ".join(task["deps"] + [task["id"]]) \
                if task["deps"] else task["id"]
            lines.append(f"{prefix} [{task['status']}]")
        return {"graph": lines, "tasks": len(tasks)}
    finally:
        store.close()


def op_agents(root: Path, team: str) -> dict:
    store, d, _team = _open_team(root, team)
    try:
        return {"agents": [
            {"id": a["id"], "role": a["role"], "status": a["status"],
             "current_task": a["current_task"], "restarts": a["restarts"],
             "heartbeat_at": a["heartbeat_at"]}
            for a in store.list_agents(team)]}
    finally:
        store.close()


def op_logs(root: Path, team: str, limit: int = 40,
            task_id: str | None = None) -> list[dict]:
    store, d, _team = _open_team(root, team)
    try:
        rows = eventlog.list_events(store.conn, team, task_id=task_id)
        return [{"seq": e["seq"], "ts": e["timestamp"],
                 "type": e["event_type"], "actor": e["actor_id"],
                 "task": e["task_id"]} for e in rows[-limit:]]
    finally:
        store.close()


def op_diff(root: Path, team: str, task_id: str) -> dict:
    store, d, _team = _open_team(root, team)
    try:
        task = store.get_task(team, task_id)
        if task is None:
            raise NotFoundError(f"task not found: {task_id}")
        meta = task.get("metadata", {})
        return {"task": task_id, "changed_files": meta.get("changed_files", []),
                "write_set_violations": meta.get("workspace_violations", []),
                "head_commit": task.get("head_commit"),
                "base_commit": task.get("base_commit")}
    finally:
        store.close()


def op_cost(root: Path, team: str) -> dict:
    from .observability import cost_of

    store, d, _team = _open_team(root, team)
    try:
        run = _current_run(store, team)
        usage = store.list_usage(team, run_id=run["id"] if run else None)
        by_task: dict[str, dict] = {}
        for record in usage:
            slot = by_task.setdefault(record["task_id"] or "-",
                                      {"tokens": 0, "cost_usd": 0.0})
            slot["tokens"] += record.get("total_tokens") or 0
            cost, _quality = cost_of(record)
            slot["cost_usd"] += cost or 0.0
        total = round(sum(v["cost_usd"] for v in by_task.values()), 6)
        return {"per_task": {k: {"tokens": v["tokens"],
                                 "cost_usd": round(v["cost_usd"], 6)}
                             for k, v in by_task.items()},
                "total_usd": total, "quality": "estimated" if total else "unknown"}
    finally:
        store.close()


def op_plan(root: Path, tasks_path: Path, *, check_fs: bool = True) -> dict:
    """Validate a spec and show the DAG without creating anything."""
    root = Path(root).resolve()
    from .validation import topological_order, validate_team_spec

    spec = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    result = validate_team_spec(spec, root, check_fs=check_fs)
    order = topological_order(spec.get("tasks", [])) if result.ok else None
    return {"ok": result.ok,
            "errors": [f"[{i.code}] {i.message}" for i in result.errors],
            "warnings": [f"[{i.code}] {i.message}" for i in result.warnings],
            "topological_order": order}


def op_workspace(root: Path, team: str, clean_task: str | None = None) -> dict:
    """List task worktrees, or clean one completed task's worktree."""
    from . import workspaces

    store, d, team_row = _open_team(root, team)
    try:
        tasks = store.list_tasks(team)
        if clean_task:
            task = store.get_task(team, clean_task)
            if task is None:
                raise NotFoundError(f"task not found: {clean_task}")
            if task["status"] != "completed":
                raise StateRejected(
                    "only completed tasks can have their workspace cleaned")
            workspaces.remove_workspace(Path(team_row["project_root"]), d, team,
                                        clean_task, keep_branch=True)
            return {"cleaned": clean_task}
        return {"workspaces": [
            {"task": t["id"], "path": t["workspace_path"], "branch": t["branch"],
             "base": t["base_commit"],
             "exists": bool(t["workspace_path"])
             and Path(t["workspace_path"]).exists()}
            for t in tasks if t.get("workspace_path")]}
    finally:
        store.close()


def op_report(root: Path, team: str, out_path: Path) -> dict:
    from .report import write_report

    store, d, _team = _open_team(root, team)
    try:
        path = write_report(store, team, Path(out_path))
        return {"report": str(path)}
    finally:
        store.close()


# ---------------------------------------------------------------- status


def op_status(root: Path, team: str) -> dict:
    store, d, team_row = _open_team(root, team)
    try:
        run = _current_run(store, team)
        tasks = store.list_tasks(team)
        unread: dict[str, int] = {}
        for agent in store.list_agents(team):
            count = len(store.list_messages(team, to_agent=agent["id"],
                                            unread_only=True))
            if count:
                unread[agent["id"]] = count
        return {"team": team, "created": team_row["created_at"],
                "run": {"id": run["id"], "number": run["number"],
                        "status": run["status"]} if run else None,
                "tasks": [{"id": t["id"], "status": t["status"],
                           "owner": t["owner"], "assignee": t["assignee"],
                           "title": t["title"],
                           "failed_rounds": t["verification_failures"],
                           "tokens": t["tokens"]} for t in tasks],
                "unread": unread}
    finally:
        store.close()


def op_inspect(root: Path, team: str, task_id: str) -> dict:
    store, d, _team = _open_team(root, team)
    try:
        task = store.get_task(team, task_id)
        if task is None:
            raise NotFoundError(f"task not found: {task_id}")
        attempts = store.list_attempts(team, task_id)
        task_events = eventlog.list_events(store.conn, team, task_id=task_id)
        task.pop("execution_token_hash", None)  # never leak hashes
        return {"task": task,
                # Every version the task submitted, each with the trusted
                # base its attempt was checked against (spec 3.2).
                "submissions": [
                    {"submission_id": record["id"],
                     "attempt": record["attempt"],
                     "commit_sha": record["commit_sha"],
                     "base_commit_sha": record["base_commit_sha"],
                     "changed_files": record["changed_files"],
                     "verification_status": record["verification_status"],
                     "evidence_status": record["evidence_status"],
                     "verifier": record["verifier"],
                     "created_at": record["created_at"]}
                    for record in store.list_submissions(team, task_id)],
                "attempts": [{k: a[k] for k in ("attempt", "verdict", "verifier",
                                                "feedback", "created_at")}
                             for a in attempts],
                "events": [{k: e[k] for k in ("seq", "event_type", "actor_id",
                                              "timestamp")}
                           for e in task_events]}
    finally:
        store.close()
