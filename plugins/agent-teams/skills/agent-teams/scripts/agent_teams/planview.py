"""Shapes the board draws: the plan as a left-to-right graph, and the
timeline of a team (v2.1 spec 6 and 7).

Both are pure functions over what the engine already recorded. Nothing
here invents a position, a time or an event: a task that was never
claimed has no bar on the timeline, and a dependency the engine does
not know is not drawn.
"""
from __future__ import annotations

# What the reader is shown for the time between one event of a task and
# the next (v2.1 spec 7: work, handover, checks, fixes, waiting,
# recovery after a failure, completion).
_AFTER = {
    "task_claimed": "work", "task_started": "work",
    "task_submitted": "check", "verification_started": "check",
    "verification_failed": "fix", "verify_fail": "fix",
    "task_released": "wait",
    "agent_timeout": "recovery", "agent_failed": "recovery",
    "verification_passed": "done", "verify_pass": "done", "task_completed": "done",
    "task_blocked": "stopped", "task_cancelled": "stopped",
}
# Marks drawn for one task; the older ones are counted, not sent (v2.1
# spec 21: the whole journal is never loaded at once).
MARK_LIMIT = 10
SPAN_TEXT = {
    "work": "работа", "check": "проверка качества", "fix": "доработка после замечаний",
    "wait": "ожидание в очереди", "recovery": "восстановление после сбоя",
    "done": "завершено", "stopped": "остановлено",
}


def plan_layout(tasks: list[dict]) -> dict:
    """Columns and rows for the plan graph.

    Tasks run left to right in the order of their dependencies; tasks
    that can run in parallel share a column and get a row each. The
    column is the length of the longest chain before the task, so a
    task never stands left of something it depends on. Rows are chosen
    to keep the lines short (near the rows of the tasks it depends
    on), and the result is stable: the same plan always lays out the
    same way.

    Returns ``{"nodes": [{"id", "col", "row"}], "edges": [{"from", "to"}],
    "columns": n, "rows": n}`` with the nodes in the order of the plan.
    """
    ids = [t["id"] for t in tasks]
    known = set(ids)
    deps = {t["id"]: [d for d in (t.get("deps") or []) if d in known] for t in tasks}

    # The longest chain before a task gives its column. A task is placed
    # only once everything it depends on is placed (Kahn's order).
    column = {task_id: 0 for task_id in ids}
    waiting = {task_id: set(deps[task_id]) for task_id in ids}
    depends_on: dict[str, list[str]] = {}
    for task_id in ids:
        for dep in deps[task_id]:
            depends_on.setdefault(dep, []).append(task_id)
    ready = [task_id for task_id in ids if not waiting[task_id]]
    while ready:
        task_id = ready.pop(0)
        for child in depends_on.get(task_id, []):
            waiting[child].discard(task_id)
            column[child] = max(column[child], column[task_id] + 1)
            if not waiting[child]:
                ready.append(child)
    # The engine rejects cycles at init. Should anything be left unplaced
    # all the same, it goes to the last column instead of being lost.
    stuck = [task_id for task_id in ids if waiting[task_id]]
    if stuck:
        width = max(column.values(), default=0) + 1
        for task_id in stuck:
            column[task_id] = width

    by_column: dict[int, list[str]] = {}
    for task_id in ids:
        by_column.setdefault(column[task_id], []).append(task_id)
    order = {task_id: index for index, task_id in enumerate(ids)}
    row: dict[str, int] = {}
    for col in sorted(by_column):
        for index, task_id in enumerate(sorted(by_column[col], key=lambda t: order[t])):
            row[task_id] = index

    def pull(task_id: str) -> float:
        """The average row of the tasks this one depends on."""
        parent_rows = [row[d] for d in deps[task_id]]
        return sum(parent_rows) / len(parent_rows) if parent_rows else 0.0

    # A few sweeps left to right move each task next to what it depends
    # on; ties keep the row the task already has, so it settles.
    for _ in range(4):
        for col in sorted(by_column):
            if col == 0:
                continue
            members = sorted(by_column[col], key=lambda t: (pull(t), row[t]))
            for index, task_id in enumerate(members):
                row[task_id] = index

    return {
        "nodes": [{"id": task_id, "col": column[task_id], "row": row[task_id]}
                  for task_id in ids],
        "edges": [{"from": dep, "to": task_id}
                  for task_id in ids for dep in deps[task_id]],
        "columns": max(column.values()) + 1 if ids else 0,
        "rows": max(row.values()) + 1 if ids else 0,
    }


def timeline(tasks: list[dict], events_by_task: dict[str, list[dict]],
             start: str, end: str) -> dict:
    """How each task spent its time, from the journal.

    ``events_by_task`` holds the reader's event items of each task,
    oldest first. Every span runs from one real event to the next (or
    to ``end``: now, or the moment the run finished). A task without
    events has no spans: the engine recorded nothing about its time.
    The spans are all sent; the marks are the last ``MARK_LIMIT`` of a
    task, and ``marks_more`` says how many older ones are not.
    """
    rows = []
    for task in tasks:
        events = [item for item in events_by_task.get(task["id"], []) if item.get("ts")]
        spans: list[dict] = []
        marks = []
        for index, item in enumerate(events):
            marks.append({"ts": item["ts"], "title": item["title"], "kind": item.get("kind"),
                          "icon": item.get("icon"), "detail": item.get("detail", "")})
            state = _AFTER.get(item.get("type"))
            if not state:
                continue        # a bookkeeping line changes nothing on the clock
            till = events[index + 1]["ts"] if index + 1 < len(events) else end
            if spans and spans[-1]["kind"] == state:
                spans[-1]["to"] = till
                continue
            spans.append({"from": item["ts"], "to": till, "kind": state,
                          "text": SPAN_TEXT[state]})
        rows.append({"id": task["id"], "title": task["title"], "state": task["state"],
                     "kind": task["kind"], "spans": spans, "marks": marks[-MARK_LIMIT:],
                     "marks_more": max(0, len(marks) - MARK_LIMIT)})
    return {"start": start, "end": end, "rows": rows, "span_text": dict(SPAN_TEXT)}
