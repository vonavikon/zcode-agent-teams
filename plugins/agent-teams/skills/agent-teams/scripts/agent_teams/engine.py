"""Engine operations: one function per CLI command, v0.3 (spec stage 1).

Every mutation runs under the team's StateLock and one Store transaction,
goes through the state machine, appends hash-chained events and afterwards
refreshes the v0.2-compatible mirrors (tasks.json, inbox/<agent>.json,
events.jsonl).

Authority model (spec section 6): the lead token printed once by init
authorizes lead-side verdicts and interventions; an execution token
printed once by claim authorizes the executor's submit. Free-text --by
values are recorded as actors, never trusted as authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import events as eventlog
from . import statemachine
from .config import merge_config
from .errors import NotFoundError, PermissionDenied, StateRejected, UsageError
from .locking import StateLock
from .storage import Store, now_iso, open_store
from .validation import NAME_RE, normalize_acceptance, validate_team_spec

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
    lead_token = secrets.token_urlsafe(24)

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

    d.mkdir(parents=True)
    with open_store(d) as store:
        with StateLock(d, "init:lead"), store.transaction():
            store.create_team({"id": team, "project_root": str(root),
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
            "lead_token": lead_token,
            "warnings": [i.message for i in result.warnings] + result_warnings}


def _last_event(store: Store, team_id: str) -> dict:
    rows = eventlog.list_events(store.conn, team_id)
    return rows[-1] if rows else {}


# ---------------------------------------------------------------- claim


def op_claim(root: Path, team: str, task_id: str, by: str) -> dict:
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, f"claim:{by}"):
            config = merge_config(team_row["config"])
            task = store.get_task(team, task_id)
            if task is None:
                raise NotFoundError(f"task not found: {task_id}")
            workspace = _prepare_workspace(root, d, team, task, config)
            with store.transaction():
                recorded = []
                agent = store.get_agent(team, by)
                if agent and agent["role"] not in ("executor", "lead"):
                    raise StateRejected(
                        f"agent {by!r} has role {agent['role']!r}: only executors "
                        "can claim tasks")
                for dep in task["deps"]:
                    dep_row = store.get_task(team, dep)
                    if dep_row is None:
                        raise NotFoundError(f"dependency not found: {dep}")
                    if dep_row["status"] != "completed":
                        raise StateRejected(
                            f"dependency {dep} is not completed "
                            f"(status={dep_row['status']})")
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
                    fields.update(workspace_path=workspace.path,
                                  branch=workspace.branch,
                                  base_commit=workspace.base_commit)
                    recorded.append(eventlog.append_event(
                        store.conn, event_type="workspace_created", team_id=team,
                        run_id=_current_run(store, team)["id"], task_id=task_id,
                        actor_id=by, metadata={"branch": workspace.branch,
                                               "base": workspace.base_commit}))
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


def _prepare_workspace(root: Path, d: Path, team: str, task: dict,
                       config: dict):
    """Create or reuse the task worktree when the team uses isolation.

    Runs under the state lock but outside the SQLite transaction: git is
    slow and must not hold the database. An orphaned worktree (a claim
    that failed later) is simply reused by the next claim.
    """
    from . import workspaces

    mode = config.get("workspace", "auto")
    if mode == "shared":
        return None
    project_root = Path(root).resolve()
    if mode == "auto" and not workspaces.is_git_project(project_root):
        return None
    if task.get("workspace_path") and Path(task["workspace_path"]).exists():
        # Reuse: crash recovery or a repeated claim after release.
        return workspaces.WorkspaceInfo(task["workspace_path"], task["branch"],
                                        task["base_commit"])
    return workspaces.create_workspace(
        project_root, d, team, task["id"], task.get("base_commit"))


# ---------------------------------------------------------------- submit


def op_submit(root: Path, team: str, task_id: str, by: str, token: str) -> dict:
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
            diff, violations = _capture_task_diff(task)
            with store.transaction():
                recorded = []
                statemachine.apply_transition(
                    store.conn, task, statemachine.TaskStatus.AWAITING_VERIFICATION,
                    event_type="task_submitted",
                    run_id=_current_run(store, team)["id"], actor_id=by,
                    initiator=statemachine.EXECUTOR, timestamp=now_iso())
                recorded.append(_last_event(store, team))
                fields = {}
                if diff is not None:
                    fields["head_commit"] = diff.head_commit
                    fields["metadata"] = {**task.get("metadata", {}),
                                          "changed_files": list(diff.changed),
                                          "workspace_violations": violations,
                                          "uncommitted_changes":
                                              diff.has_uncommitted}
                if fields:
                    store.update_task(team, task_id, **fields)
                if violations:
                    recorded.append(eventlog.append_event(
                        store.conn, event_type="workspace_conflict",
                        team_id=team, run_id=_current_run(store, team)["id"],
                        task_id=task_id, actor_id=by,
                        metadata={"violations": violations}))
                store.update_agent(team, by, status="waiting_verification",
                                   current_task=task_id)
                _recorded(store, team, recorded)
                _refresh_mirrors(store, team)
            result = {"task": task_id, "status": "awaiting_verification",
                      "by": by}
            if diff is not None:
                result["changed_files"] = len(diff.changed)
                result["write_set_violations"] = violations
            return result
    finally:
        store.close()


def _capture_task_diff(task: dict):
    """Diff of the task worktree versus its base, plus write-set violations.

    Returns (None, []) for teams without workspace isolation.
    """
    from . import workspaces

    ws_path = task.get("workspace_path")
    base = task.get("base_commit")
    if not ws_path or not base or not Path(ws_path).exists():
        return None, []
    diff = workspaces.capture_diff(Path(ws_path), base)
    violations = workspaces.write_set_violations(diff, task["files_write"])
    return diff, violations


# ---------------------------------------------------------------- verify


def op_verify(root: Path, team: str, task_id: str, verdict: str,
              feedback: str, lead_token: str, *, by: str = "verifier",
              report: dict | None = None) -> dict:
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

                if verdict == "pass":
                    store.add_attempt({
                        "id": uuid.uuid4().hex, "team_id": team, "run_id": run_id,
                        "task_id": task_id, "attempt": attempt_no, "verifier": by,
                        "actor": "lead", "verdict": "pass",
                        "report": report or {}, "feedback": ""})
                    statemachine.apply_transition(
                        store.conn, task, statemachine.TaskStatus.COMPLETED,
                        event_type="verification_passed", run_id=run_id,
                        actor_id=by, initiator=statemachine.GATE,
                        timestamp=now_iso(),
                        metadata={"attempt": attempt_no})
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
                    failures = task["verification_failures"] + 1
                    store.add_attempt({
                        "id": uuid.uuid4().hex, "team_id": team, "run_id": run_id,
                        "task_id": task_id, "attempt": attempt_no, "verifier": by,
                        "actor": "lead", "verdict": "fail",
                        "report": report or {}, "feedback": feedback})
                    store.update_task(team, task_id, verification_failures=failures)
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
    store, d, team_row = _open_team(root, team)
    try:
        with StateLock(d, "cancel:lead"):
            _require_lead(team_row, lead_token)
            with store.transaction():
                task = store.get_task(team, task_id)
                if task is None:
                    raise NotFoundError(f"task not found: {task_id}")
                statemachine.apply_transition(
                    store.conn, task, statemachine.TaskStatus.CANCELLED,
                    event_type="task_cancelled",
                    run_id=_current_run(store, team)["id"], actor_id="lead",
                    initiator=statemachine.LEAD, timestamp=now_iso(),
                    reason=reason or "cancelled by the lead")
                _recorded(store, team, [_last_event(store, team)])
                _refresh_mirrors(store, team)
            return {"task": task_id, "status": "cancelled"}
    finally:
        store.close()


def op_recover(root: Path, team: str, lead_token: str,
               task_id: str | None = None) -> dict:
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
                    lease = task.get("lease_expires_at")
                    stale = lease and _parse_iso(lease) < now
                    if not stale:
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
                        metadata={"restarts": restarts})]
                    statemachine.apply_transition(
                        store.conn, task, statemachine.TaskStatus.BLOCKED,
                        event_type="task_blocked", run_id=run_id,
                        actor_id="recovery", initiator=statemachine.RECOVERY,
                        timestamp=now_iso(),
                        reason="execution lease expired (agent lost or stuck)")
                    recorded.append(_last_event(store, team))
                    statemachine.apply_transition(
                        store.conn, {**task, "status": "blocked"},
                        statemachine.TaskStatus.PENDING,
                        event_type="task_released", run_id=run_id,
                        actor_id="recovery", initiator=statemachine.RECOVERY,
                        timestamp=now_iso(), reason="lease expired, back to queue")
                    recorded.append(_last_event(store, team))
                    store.update_task(team, task["id"], restarts=restarts)
                    _recorded(store, team, recorded)
                    recovered.append({"task": task["id"], "action": "requeued",
                                      "restarts": restarts})
                _refresh_mirrors(store, team)
            return {"recovered": recovered}
    finally:
        store.close()


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
                store.add_usage({
                    "team_id": team, "run_id": run["id"] if run else "",
                    "task_id": task_id, "agent_id": by, "model": model,
                    "total_tokens": add, "source": source,
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
                "attempts": [{k: a[k] for k in ("attempt", "verdict", "verifier",
                                                "feedback", "created_at")}
                             for a in attempts],
                "events": [{k: e[k] for k in ("seq", "event_type", "actor_id",
                                              "timestamp")}
                           for e in task_events]}
    finally:
        store.close()
