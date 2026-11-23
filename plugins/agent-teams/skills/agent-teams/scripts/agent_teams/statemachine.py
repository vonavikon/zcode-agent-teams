"""Task statuses and the one place where a status may change.

Every status change of a task goes through :func:`apply_transition`. It
checks the edge against ALLOWED_TRANSITIONS, checks who initiates it,
writes the row with a guard on the previous status and appends the event.
No other code path edits ``tasks.status``.

Edges (source -> target: who may initiate)::

    pending              -> in_progress            executor, lead
    pending              -> cancelled              lead
    in_progress          -> awaiting_verification  executor
    in_progress          -> blocked                recovery, lead
    awaiting_verification-> in_progress            gate (FAIL below the limit)
    awaiting_verification-> completed              gate (PASS)
    awaiting_verification-> blocked                gate (FAIL at the limit)
    blocked              -> pending                lead, recovery

``completed`` and ``cancelled`` are terminal. A completed task is never
reopened: to redo the work the lead starts a new run or adds a task.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Mapping

from . import events
from .errors import StateRejected


class TaskStatus(str, Enum):
    """Lifecycle of a task.

    ``blocked`` means the task cannot proceed until the lead or the
    recovery decides what happens next.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_VERIFICATION = "awaiting_verification"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = frozenset(TaskStatus) - {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})

# Kinds of initiator, used by the role checks of the engine.
EXECUTOR, LEAD, RECOVERY, GATE = "executor", "lead", "recovery", "gate"

P, IP, AV, DONE, BLOCKED, CANCELLED = tuple(TaskStatus)

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    P: frozenset({IP, CANCELLED}),
    IP: frozenset({AV, BLOCKED}),
    AV: frozenset({IP, DONE, BLOCKED}),
    BLOCKED: frozenset({P}),
    DONE: frozenset(),
    CANCELLED: frozenset(),
}

TRANSITION_INITIATORS: dict[tuple[TaskStatus, TaskStatus], frozenset[str]] = {
    (P, IP): frozenset({EXECUTOR, LEAD}),
    (P, CANCELLED): frozenset({LEAD}),
    (IP, AV): frozenset({EXECUTOR}),
    (IP, BLOCKED): frozenset({RECOVERY, LEAD}),
    (AV, IP): frozenset({GATE}),
    (AV, DONE): frozenset({GATE}),
    (AV, BLOCKED): frozenset({GATE}),
    (BLOCKED, P): frozenset({LEAD, RECOVERY}),
}

assert set(ALLOWED_TRANSITIONS) == set(TaskStatus)
assert {(s, t) for s, targets in ALLOWED_TRANSITIONS.items() for t in targets} == set(
    TRANSITION_INITIATORS
), "every edge must name its initiators"


def _claim_fields(ts: str) -> dict[str, Any]:
    return {"claimed_at": ts, "heartbeat_at": ts}


def _submit_fields(ts: str) -> dict[str, Any]:
    return {"submitted_at": ts}


def _complete_fields(ts: str) -> dict[str, Any]:
    return {"completed_at": ts, "execution_token_hash": None}


def _block_fields(ts: str) -> dict[str, Any]:
    return {"blocked_at": ts, "execution_token_hash": None}


def _release_fields(ts: str) -> dict[str, Any]:
    # Back in the queue: the next claim starts a new execution attempt.
    return {
        "owner": None, "execution_token_hash": None, "claimed_at": None,
        "submitted_at": None, "heartbeat_at": None, "lease_expires_at": None,
        "blocked_at": None, "blocked_reason": None,
    }


def _cancel_fields(ts: str) -> dict[str, Any]:
    return {"cancelled_at": ts}


_ON_ENTER: dict[TaskStatus, Callable[[str], dict[str, Any]]] = {
    IP: _claim_fields,
    AV: _submit_fields,
    DONE: _complete_fields,
    BLOCKED: _block_fields,
    P: _release_fields,
    CANCELLED: _cancel_fields,
}


def can_transition(source: TaskStatus, target: TaskStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[TaskStatus(source)]


def assert_transition(source: TaskStatus, target: TaskStatus, *, task_id: str = "") -> None:
    """Raise StateRejected when the edge is not allowed."""
    source, target = TaskStatus(source), TaskStatus(target)
    if not can_transition(source, target):
        targets = ", ".join(sorted(t.value for t in ALLOWED_TRANSITIONS[source])) or "none (terminal)"
        raise StateRejected(
            f"task {task_id}: status change {source.value} -> {target.value} is not allowed "
            f"(allowed from {source.value}: {targets})",
            detail={"task": task_id, "from": source.value, "to": target.value,
                    "allowed": sorted(t.value for t in ALLOWED_TRANSITIONS[source])},
        )


def apply_transition(
    conn,
    task: Mapping[str, Any],
    target: TaskStatus,
    *,
    event_type: str,
    run_id: str,
    actor_id: str,
    initiator: str,
    timestamp: str,
    reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Move ``task`` to ``target`` inside the caller's open transaction.

    ``task`` is the current row as a dict (``team_id``, ``id`` and
    ``status`` are read). Returns the row as it is after the change.
    """
    source, target = TaskStatus(task["status"]), TaskStatus(target)
    task_id = str(task["id"])
    assert_transition(source, target, task_id=task_id)

    allowed = TRANSITION_INITIATORS[(source, target)]
    if initiator not in allowed:
        raise StateRejected(
            f"task {task_id}: {source.value} -> {target.value} cannot be initiated by "
            f"{initiator} (allowed: {', '.join(sorted(allowed))})",
            detail={"task": task_id, "initiator": initiator, "allowed": sorted(allowed)},
        )
    if target is BLOCKED and not reason:
        raise StateRejected(f"task {task_id}: blocking a task requires a reason",
                            detail={"task": task_id})

    assignments: dict[str, Any] = {"status": target.value, "last_event_ts": timestamp}
    if not (source is AV and target is IP):
        # A FAIL returns the task to the same execution attempt: claim
        # timestamps stay as they are.
        assignments.update(_ON_ENTER[target](timestamp))
    if target is BLOCKED:
        assignments["blocked_reason"] = reason

    # The guard on the previous status turns a lost update (a writer that
    # bypassed the state lock) into a visible rejection.
    columns = ", ".join(f"{name} = ?" for name in assignments)
    params = [timestamp, *assignments.values(), task["team_id"], task_id, source.value]
    cursor = conn.execute(
        f"UPDATE tasks SET {columns}, updated_at = ? "
        f"WHERE team_id = ? AND id = ? AND status = ?",
        params,
    )
    if cursor.rowcount != 1:
        raise StateRejected(
            f"task {task_id}: status is no longer {source.value} (changed by another writer)",
            detail={"task": task_id, "expected": source.value},
        )

    event_meta: dict[str, Any] = dict(metadata or {})
    event_meta.update({"from": source.value, "to": target.value, "initiator": initiator})
    if reason:
        event_meta["reason"] = reason
    events.append_event(
        conn, event_type=event_type, team_id=task["team_id"], run_id=run_id,
        task_id=task_id, actor_id=actor_id, timestamp=timestamp, metadata=event_meta,
    )
    return {**task, **assignments, "updated_at": timestamp}


def status_values() -> list[str]:
    return [status.value for status in TaskStatus]
