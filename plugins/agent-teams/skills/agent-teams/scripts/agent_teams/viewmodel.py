"""The user-facing model: raw engine state translated for people.

The dashboard never shows the engine's vocabulary. This module is the
single place that turns tasks, agents, events and runs into the words a
non-technical reader understands (spec v2.1, sections 1.2, 2.1, 2.2).

Two levels exist on purpose. ``simple`` carries human labels and no
identifier longer than a display name; ``advanced`` keeps the technical
fields beside every human one. Both are built from the same snapshot, so
switching the level never changes team state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .observability import cost_of

# Task status as a reader should see it. Every entry carries text and an
# icon name: state is never conveyed by color alone.
TASK_STATE = {
    "pending": {"label": "В очереди", "hint": "ещё не начата",
                "kind": "idle", "icon": "circle"},
    "in_progress": {"label": "В работе", "hint": "исполнитель занимается задачей",
                    "kind": "busy", "icon": "progress"},
    "awaiting_verification": {"label": "На проверке", "hint": "результат готов, идёт проверка качества",
                              "kind": "attention", "icon": "search"},
    "completed": {"label": "Принято", "hint": "проверено и принято",
                  "kind": "done", "icon": "check"},
    "blocked": {"label": "Нужно ваше внимание",
                "hint": "без решения человека работа не продолжится",
                "kind": "problem", "icon": "alert"},
    "cancelled": {"label": "Отменена", "hint": "задача снята",
                  "kind": "idle", "icon": "minus"},
}

# The agent's state is not the task's state: an executor whose task is on
# verification is waiting, an executor with nothing claimed is free.
AGENT_STATE = {
    "free": {"label": "Свободен", "hint": "готов к следующей задаче", "icon": "circle"},
    "working": {"label": "Работает", "hint": "выполняет задачу", "icon": "progress"},
    "verifying": {"label": "Проверяет", "hint": "проверяет результат другого специалиста",
                  "icon": "search"},
    "waiting": {"label": "Ожидает", "hint": "ждёт результат проверки или другие задачи",
                "icon": "hourglass"},
    "recovering": {"label": "Восстанавливается",
                   "hint": "система передаёт работу дальше", "icon": "refresh"},
    "done": {"label": "Завершил", "hint": "все его задачи приняты", "icon": "check"},
    "problem": {"label": "Проблема", "hint": "не может продолжать без решения человека",
                "icon": "alert"},
}

RUN_STATE = {
    "active": {"label": "В работе", "hint": "команда выполняет задачи"},
    "integration_running": {"label": "Объединение результатов",
                            "hint": "отдельные части собираются в общий результат"},
    "integration_failed": {"label": "Нужно ваше внимание",
                           "hint": "не удалось объединить результаты, исходные версии сохранены"},
    "apply_pending": {"label": "Готово к применению",
                      "hint": "результат собран и проверен, изменения ещё не внесены в проект"},
    "finished": {"label": "Завершено", "hint": "результат применён к проекту"},
}

# Event types the feed shows, in the reader's words. Events that carry no
# meaning for a reader are left out rather than translated into noise.
# The v0.2 names are listed so an old log reads the same as a new one.
EVENT_TEXT = {
    "team_created": "Команда начала работу",
    "run_started": "Запуск команды начался",
    "run_finished": "Запуск команды завершён",
    "task_created": "Задача добавлена в план",
    "task_assigned": "Задача закреплена за специалистом",
    "task_claimed": "Приступил к задаче",
    "task_started": "Начал выполнение",
    "task_submitted": "Передал результат на проверку",
    "verification_started": "Началась проверка результата",
    "verification_passed": "Проверка пройдена",  # overridden ones are reworded below
    "verify_pass": "Проверка пройдена",
    "verification_failed": "Проверка не пройдена, результат отправлен на доработку",
    "verify_fail": "Проверка не пройдена, результат отправлен на доработку",
    "task_completed": "Этап завершён",
    "task_blocked": "Задача остановлена: нужно решение человека",
    "round_limit": "Задача остановлена: нужно решение человека",
    "task_released": "Задача возвращена в очередь",
    "task_cancelled": "Задача отменена",
    "agent_started": "Специалист приступил к работе",
    "agent_finished": "Специалист закончил работу",
    "agent_failed": "Специалист перестал отвечать",
    "agent_timeout": "Специалист перестал отвечать",
    "workspace_created": "Подготовлена рабочая область",
    "workspace_integrated": "Результаты объединены",  # apply is reworded below
    "workspace_conflict": "Результат выходит за разрешённые границы",
    "token_usage_recorded": "Учтён объём использования AI",
    "task_tokens": "Учтён объём использования AI",
    "message_sent": "Отправлено сообщение",
    "budget_changed": "Руководитель команды изменил бюджет",
    "run_paused": "Работа поставлена на паузу: новые задачи не запускаются",
    "run_resumed": "Пауза снята: новые задачи запускаются",
    "lead_token_rotated": "Ключ руководителя команды заменён",
    "team_completed": "Работа команды завершена",
}

# Actors that stand for a function, not for a specialist. None of these
# names reaches the reader as the raw identifier the engine stores.
_FUNCTION_ACTORS = {"lead": "Руководитель команды", "recovery": "Система",
                    "owner": "Владелец проекта"}
VERIFIER_ROLE = "AI-проверяющий"
_VERIFIER_EVENTS = ("verification_started", "verification_passed",
                    "verification_failed", "verify_pass", "verify_fail")
# How the feed marks an event: a kind sets the tone of its icon, so a
# state is read from the picture and the text, never from color alone.
_EVENT_MARKS = {
    "team_created": ("normal", "circle"), "run_started": ("normal", "circle"),
    "run_finished": ("done", "check"),
    "task_created": ("normal", "circle"), "task_assigned": ("normal", "circle"),
    "task_claimed": ("busy", "progress"), "task_started": ("busy", "progress"),
    "task_submitted": ("busy", "search"), "verification_started": ("busy", "search"),
    "verification_passed": ("done", "check"), "verify_pass": ("done", "check"),
    "verification_failed": ("problem", "alert"), "verify_fail": ("problem", "alert"),
    "task_completed": ("done", "check"),
    "task_blocked": ("problem", "alert"), "round_limit": ("problem", "alert"),
    "task_released": ("normal", "refresh"), "task_cancelled": ("normal", "minus"),
    "agent_started": ("busy", "progress"), "agent_finished": ("done", "check"),
    "agent_failed": ("problem", "alert"), "agent_timeout": ("problem", "alert"),
    "workspace_created": ("busy", "progress"),
    "workspace_integrated": ("done", "check"),
    "workspace_conflict": ("problem", "alert"),
    "token_usage_recorded": ("normal", "circle"), "task_tokens": ("normal", "circle"),
    "message_sent": ("normal", "circle"),
    "budget_changed": ("manual", "refresh"),
    "run_paused": ("attention", "minus"), "run_resumed": ("normal", "refresh"),
    "lead_token_rotated": ("manual", "refresh"),
    "team_completed": ("done", "check"),
}
# Bookkeeping that would only repeat the line next to it: one claim writes
# three events, a usage record is accounting. The advanced level shows all.
_ADVANCED_ONLY = frozenset({"task_created", "workspace_created", "agent_started",
                            "token_usage_recorded", "task_tokens"})
# The longest remark of a checker the reader is shown in full.
REMARK_LIMIT = 500


def _event_meta(event: dict) -> dict:
    meta = event.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _times(n: int | None) -> str:
    """A count with the right ending: 1 раз, 2 раза, 5 раз."""
    if n is None:
        return "несколько раз"
    last, tenth = n % 10, n % 100
    if last == 1 and tenth != 11:
        return f"{n} раз"
    if last in (2, 3, 4) and tenth not in (12, 13, 14):
        return f"{n} раза"
    return f"{n} раз"


def _remark(text) -> str:
    return str(text or "")[:REMARK_LIMIT]


def parse_time(value: str | None) -> datetime | None:
    """An ISO timestamp of the engine as an aware datetime.

    The engine writes UTC with a zone. A timestamp without one (an older
    row, a hand edit, a SQLite ``datetime()``) is read as UTC: that is
    the clock the engine keeps. Anything unreadable is None, never a
    crash: one bad cell must not take the whole board down.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ago(iso: str | None, now: datetime) -> int | None:
    """Seconds since an ISO timestamp; None when the timestamp is absent."""
    then = parse_time(iso)
    if then is None:
        return None
    return max(0, int((now - then).total_seconds()))


def _task_state(task: dict, now: datetime, threshold_s: float) -> dict:
    """The task's state as a reader should see it.

    An executor silent for longer than ``threshold_s`` (the engine's own
    heartbeat limit) makes the task "not answering": it is still
    ``in_progress`` for the engine, but "in work" would be a lie to the
    reader, and the attention card already asks about it.
    """
    state = TASK_STATE.get(task["status"], TASK_STATE["pending"])
    silent_s = _silent_s(task, now)
    if silent_s is not None and silent_s > threshold_s:
        return {"label": "Не отвечает", "hint": "исполнитель долго не выходит на связь",
                "kind": "problem", "icon": "alert"}
    return state


def _silent_s(task: dict, now: datetime) -> int | None:
    """How long the executor of a task has been silent.

    Heartbeat is the honest signal when it exists. Tasks that were never
    claimed have no silence at all, which is different from zero.
    """
    if task.get("status") not in ("in_progress", "awaiting_verification"):
        return None
    return _ago(task.get("heartbeat_at") or task.get("claimed_at"), now)


# Role vocabulary. The spec says roles are defined by the task and its
# context, so a known lens maps to a proper role name and anything else
# falls back to the generic specialist rather than exposing a raw word.
_ROLE_BY_LENS = {
    "security": "AI-специалист по безопасности",
    "docs": "AI-редактор документации", "documentation": "AI-редактор документации",
    "docs-commands": "AI-редактор документации", "docs-behavior": "AI-аналитик",
    "docs-i18n": "AI-редактор документации", "translations": "AI-редактор документации",
    "ux": "AI-дизайнер интерфейса", "engine": "AI-разработчик движка",
    "engine-api": "AI-разработчик движка", "storage": "AI-разработчик",
    "workspaces": "AI-разработчик", "verification": "AI-разработчик",
    "platform": "AI-инженер платформы", "behavior": "AI-аналитик",
    "correctness": "AI-разработчик", "robustness": "AI-инженер",
    "performance": "AI-аналитик производительности", "coverage": "AI-аналитик",
    "synthesis": "AI-аналитик",
}


_ROLE_DOES = {
    "AI-специалист по безопасности": "Ищет риски и слабые места, проверяет защиту.",
    "AI-редактор документации": "Пишет и проверяет документы и инструкции.",
    "AI-дизайнер интерфейса": "Проектирует экраны и удобство работы.",
    "AI-разработчик движка": "Пишет внутреннюю логику и правила работы.",
    "AI-разработчик": "Пишет код и исправляет ошибки.",
    "AI-инженер платформы": "Отвечает за то, на чём всё работает: запуск, обновления, совместимость.",
    "AI-аналитик": "Собирает и сравнивает данные, делает выводы.",
    "AI-аналитик производительности": "Ищет, где работа идёт медленно, и ускоряет.",
    "AI-инженер": "Проверяет, что результат выдержит реальные условия.",
    VERIFIER_ROLE: "Проверяет результаты других специалистов, прежде чем они будут приняты.",
    "AI-специалист": "Выполняет задачи по своему профилю.",
}
# How a criterion is checked, in the reader's words. The machine checks
# (a command, a file, a security scanner) are kept apart from judgement
# of content: they do not mean the same thing (spec 10.3).
_CRITERION_HOW = {
    "test": "автоматическая проверка: запускается команда",
    "file_check": "автоматическая проверка: нужен файл",
    "security_check": "автоматическая проверка: безопасность",
    "code_review": "оценка содержания проверяющим",
    "documentation": "оценка содержания проверяющим",
    "manual": "решение человека",
}


def role_name(task: dict | None = None) -> str:
    """A human role derived from the task, never from a raw identifier."""
    lens = (task or {}).get("lens")
    if lens:
        return _ROLE_BY_LENS.get(str(lens).lower(), "AI-специалист")
    return "AI-специалист"


def role_does(role: str) -> str:
    """What the role is responsible for, in one line."""
    return _ROLE_DOES.get(role, _ROLE_DOES["AI-специалист"])


def _next_step(task: dict, by_id: dict | None, silent: bool = False) -> str:
    """What happens to a task next, from its status and its dependencies."""
    status = task["status"]
    if status == "completed":
        return "Готово, результат принят"
    if status == "cancelled":
        return "Задача снята"
    if status == "blocked":
        return "Ждёт вашего решения"
    if status == "awaiting_verification":
        return "Идёт проверка качества"
    if status == "in_progress":
        if silent:
            return "Ждёт ответа исполнителя; без ответа система вернёт задачу в очередь"
        return "Специалист работает"
    waiting = []
    for dep in task.get("deps") or []:
        dep_task = (by_id or {}).get(dep)
        if dep_task is None or dep_task.get("status") not in ("completed", "cancelled"):
            waiting.append(dep_task["title"] if dep_task else "другой этап")
    if waiting:
        return "Ждёт завершения: " + ", ".join(waiting)
    return "Ожидает исполнителя"


def number_duplicates(names: dict[str, str]) -> dict[str, str]:
    """Tell specialists of one role apart: a name that repeats gets a number."""
    count: dict[str, int] = {}
    for name in names.values():
        count[name] = count.get(name, 0) + 1
    seen: dict[str, int] = {}
    out = {}
    for key, name in names.items():
        seen[name] = seen.get(name, 0) + 1
        out[key] = f"{name} {seen[name]}" if count[name] > 1 else name
    return out


def _actor_name(event_type, actor, roles: dict | None, meta: dict,
                task_id=None) -> str:
    """Who did it, as a role.

    An executor with tasks of different lenses wears several hats: on a
    task's own event it is named by THAT task, so the feed and the task
    card say the same name. ``roles`` is keyed by (agent id, task id)
    with a (agent id, None) entry for the specialist as a whole.
    """
    if actor in _FUNCTION_ACTORS:
        return _FUNCTION_ACTORS[actor]
    if event_type in _VERIFIER_EVENTS or (event_type in ("task_blocked", "round_limit")
                                          and "failures" in meta):
        # The gate writes the block at the verification limit.
        return VERIFIER_ROLE
    if not actor:
        return ""
    named = (roles or {})
    return named.get((actor, task_id)) or named.get((actor, None)) or "AI-специалист"


def task_view(task: dict, *, level: str = "simple", now: datetime | None = None,
              attempts: list[dict] | None = None,
              titles: dict | None = None, threshold_s: float = 900.0,
              by_id: dict | None = None) -> dict:
    """One task as the dashboard shows it.

    ``titles`` turns the ids of the tasks this one depends on into their
    titles; without it the dependency stays a raw id in ``tech`` only.

    An executor that stopped answering for longer than ``threshold_s``
    (the engine's own heartbeat limit) is shown as such. The task is
    still ``in_progress`` for the engine, but "in work" would be a lie
    to the reader, and the attention card already asks about it.
    """
    now = now or datetime.now(timezone.utc)
    meta = task.get("metadata") or {}
    deps = list(task.get("deps") or [])
    silent_s = _silent_s(task, now)
    state = _task_state(task, now, threshold_s)
    silent = state["label"] == "Не отвечает"
    view = {
        "id": task["id"],
        "title": task["title"],
        "state": state["label"],
        "state_hint": state["hint"],
        "kind": state["kind"],
        "icon": state["icon"],
        "role": role_name(task),
        "attempt": task.get("attempt", 0),
        "failed_checks": task.get("verification_failures", 0),
        "needs_attention": task["status"] == "blocked",
        # Started and not finished yet, whatever the executor's condition.
        "active": task["status"] in ("in_progress", "awaiting_verification"),
        "changed_files": list(meta.get("changed_files") or []),
        "violations": list(meta.get("workspace_violations") or []),
        "deps": [(titles or {}).get(d, "") for d in deps] if titles is not None else deps,
        "silent_s": silent_s,
        "silent": silent,
        "started_at": task.get("claimed_at"),
        "finished_at": task.get("completed_at"),
        # What is expected and how it is judged, in words (spec 4.4, 8.1).
        "description": ((task.get("spec") or {}).get("description") or ""),
        "expected": list(task.get("files_write") or []),
        "criteria": [{"text": str(c.get("description") or ""),
                      "how": _CRITERION_HOW.get(c.get("type"), "решение человека")}
                     for c in (task.get("acceptance") or []) if isinstance(c, dict)],
        "next_step": _next_step(task, by_id, silent),
        # The checkpoints the engine really records; no percentage is
        # invented between them (spec 4.2: no precision beyond the data).
        "checkpoints": {
            "started": bool(task.get("claimed_at") or task.get("attempt")),
            "submitted": bool(task.get("submitted_at"))
                         or task["status"] in ("awaiting_verification", "completed"),
            "accepted": task["status"] == "completed",
        },
    }
    if task["status"] == "completed" and attempts and any(a.get("manual") for a in attempts):
        # "Checked and accepted" would be a lie: a decision accepted it.
        view["state_hint"] = "принято решением руководителя команды, без проверки"
    started, finished = parse_time(task.get("claimed_at")), parse_time(task.get("completed_at"))
    if started:
        view["worked_s"] = max(0, int(((finished if task["status"] == "completed"
                                        and finished else now) - started).total_seconds()))
    else:
        view["worked_s"] = None
    if attempts is not None:
        view["checks"] = [{
            "n": a.get("attempt"),
            "passed": a.get("verdict") == "pass",
            "manual": bool(a.get("manual")),  # accepted by a decision, not by a check
            "feedback": _remark(a.get("feedback")),
        } for a in attempts]
    if level == "advanced":
        view["tech"] = {
            "task_id": task["id"], "status": task["status"],
            "assignee": task.get("assignee"), "owner": task.get("owner"),
            "lens": task.get("lens"),
            "branch": task.get("branch"), "base_commit": task.get("base_commit"),
            "trusted_base_commit": task.get("trusted_base_commit"),
            "draft_checkpoint_commit": task.get("draft_checkpoint_commit"),
            "accepted_commit_sha": task.get("accepted_commit_sha"),
            "head_commit": task.get("head_commit"),
            "workspace_path": task.get("workspace_path"),
            "files_read": task.get("files_read"), "files_write": task.get("files_write"),
            "acceptance": task.get("acceptance"),
            "deps": deps, "restarts": task.get("restarts"),
            "files_write": task.get("files_write"), "criteria": task.get("acceptance"),
            "heartbeat_at": task.get("heartbeat_at"),
            "lease_expires_at": task.get("lease_expires_at"),
        }
    return view


def _agent_state(agent: dict, tasks: list[dict], *, stale_s: int | None,
                 threshold_s: float) -> tuple[str, str]:
    """The agent's own state and one line about what it is doing.

    The engine keeps three agent statuses (idle, running,
    waiting_verification), so what the agent is doing is read from its
    tasks; the heartbeat is the only signal that it stopped responding.
    """
    mine = [t for t in tasks if t.get("owner") == agent["id"]
            and t["status"] in ("in_progress", "awaiting_verification")]
    if stale_s is not None and stale_s > threshold_s and mine:
        return "problem", "перестал отвечать"
    if any(t["status"] == "awaiting_verification" for t in mine):
        t = next(t for t in mine if t["status"] == "awaiting_verification")
        return "waiting", f"ждёт проверки: {t['title']}"
    if any(t["status"] == "in_progress" for t in mine):
        t = next(t for t in mine if t["status"] == "in_progress")
        return "working", f"работает над: {t['title']}"
    blocked = [t for t in tasks if t.get("assignee") == agent["id"]
               and t["status"] == "blocked"]
    if blocked and not mine:
        return "problem", f"задача остановлена: {blocked[0]['title']}"
    done = [t for t in tasks if t.get("assignee") == agent["id"]
            and t["status"] == "completed"]
    if done and len(done) == len([t for t in tasks
                                  if t.get("assignee") == agent["id"]
                                  and t["status"] != "cancelled"]):
        return "done", "все его задачи приняты"
    return "free", "готов к следующей задаче"


def agent_view(agent: dict, tasks: list[dict], *, level: str = "simple",
               now: datetime | None = None, threshold_s: float = 900.0,
               name: str | None = None) -> dict:
    """One specialist as the dashboard shows it.

    ``name`` overrides the role name (a numbered one when two
    specialists share a role)."""
    now = now or datetime.now(timezone.utc)
    # Silence is read from the agent's open tasks, the same signal the
    # engine's recovery uses. The agent row keeps its own heartbeat
    # column, but nothing fills it when a task is merely claimed.
    open_tasks = [t for t in tasks if t.get("owner") == agent["id"]
                  and t["status"] in ("in_progress", "awaiting_verification")]
    silences = [s for s in (_silent_s(t, now) for t in open_tasks) if s is not None]
    silent = min(silences) if silences else None  # a free agent is not silent
    key, doing = _agent_state(agent, tasks, stale_s=silent, threshold_s=threshold_s)
    state = AGENT_STATE[key]
    mine = [t for t in tasks if t.get("assignee") == agent["id"]]
    live = [t for t in mine if t["status"] != "cancelled"]
    starts = [parse_time(t.get("claimed_at")) for t in open_tasks]
    starts = [start for start in starts if start]
    view = {
        "name": name or role_name(mine[0] if mine else None),
        "does": role_does(role_name(mine[0] if mine else None)),
        "state": state["label"],
        "state_hint": state["hint"],
        "icon": state["icon"],
        "doing": doing,
        "silent_s": silent,
        "worked_s": max(0, int((now - min(starts)).total_seconds())) if starts else None,
        "completed": len([t for t in mine if t["status"] == "completed"]),
        "total": len(live),
        "checks": None,
        "tasks": [{"id": t["id"], "title": t["title"], "role": role_name(t),
                   "state": _task_state(t, now, threshold_s)["label"],
                   "kind": _task_state(t, now, threshold_s)["kind"]}
                  for t in live],
    }
    if level == "advanced":
        view["tech"] = {"agent_id": agent["id"], "role": agent.get("role"),
                        "status": agent.get("status"),
                        "current_task": agent.get("current_task"),
                        "heartbeat_at": agent.get("heartbeat_at"),
                        "restarts": agent.get("restarts")}
    return view


def verifier_view(tasks: list[dict], attempts: list[dict],
                 manual_tasks: set[str] | None = None) -> dict:
    """The checker as a specialist of the team (spec 5.1).

    The engine keeps no agent row for a checker without tasks of its
    own; what it did is in the verification attempts. A result the lead
    accepted by decision is not the checker's doing: the engine records
    it as a passed attempt, and it is left out of the count here.
    """
    waiting = [t for t in tasks if t["status"] == "awaiting_verification"]
    key = "verifying" if waiting else "free"
    state = AGENT_STATE[key]
    # Only the checks the checker really made: an attempt the engine
    # records behind a decision of the lead is not one, whatever its
    # verdict says.
    real = [a for a in attempts if a.get("task_id") not in (manual_tasks or set())]
    return {
        "name": VERIFIER_ROLE,
        "does": role_does(VERIFIER_ROLE),
        "state": state["label"], "state_hint": state["hint"], "icon": state["icon"],
        "doing": f"проверяет: {waiting[0]['title']}" if waiting else "ждёт результатов для проверки",
        "silent_s": None, "worked_s": None,
        "completed": len([a for a in real if a.get("verdict") == "pass"]),
        "total": len(real), "checks": len(real), "tasks": [],
    }


def event_view(event: dict, *, level: str = "simple", titles: dict | None = None,
               roles: dict | None = None, quarantined: bool = False,
               after_override: bool = False, archive_note: bool = False) -> dict | None:
    """One feed item, or None when the event means nothing to a reader.

    ``titles`` maps a task id to its title and ``roles`` an
    (agent id, task id) pair to a role name, so no raw identifier reaches
    the simple level.
    ``quarantined`` says that the workspace of this task's previous
    attempt was archived. ``after_override`` marks the journal half of a
    manual acceptance (the engine writes two events for one decision).
    ``archive_note`` marks the other line the engine writes for one lost
    executor: after the timeout it records the archive of the workspace
    under the same event type. One loss is one line for a reader.
    """
    etype = event.get("event_type")
    text = EVENT_TEXT.get(etype)
    if text is None or (level != "advanced" and etype in _ADVANCED_ONLY):
        return None
    if etype == "agent_timeout" and archive_note:
        if level != "advanced":
            return None
        return {"seq": event.get("seq"), "ts": event.get("timestamp"),
                "title": "Рабочая область прежней попытки убрана в архив",
                "type": etype, "kind": "normal", "icon": "refresh",
                "specialist": _FUNCTION_ACTORS["recovery"],
                "task": (titles or {}).get(event.get("task_id")),
                "detail": "Служебная запись: то, что исполнитель не успел сдать, лежит в архиве.",
                "bookkeeping": True}
    meta = _event_meta(event)
    if after_override:
        # The engine records a manual acceptance twice: the decision of
        # the lead, then the state change it caused. The second event has
        # the type and the shape of a passed check. A reader must never
        # see "check passed" for work that was accepted without one.
        if level != "advanced":
            return None
        return {"seq": event.get("seq"), "ts": event.get("timestamp"),
                "title": "Запись о принятии вручную", "type": etype,
                "kind": "manual", "icon": "refresh",
                "specialist": _actor_name(etype, event.get("actor_id"), roles, meta,
                                          event.get("task_id")),
                "task": (titles or {}).get(event.get("task_id")),
                "detail": "Служебная запись: результат принят решением руководителя команды, проверка не выполнялась.",
                "tech": {"event_id": event.get("event_id"), "run_id": event.get("run_id"),
                         "hash": event.get("hash"), "task_id": event.get("task_id"),
                         "actor_id": event.get("actor_id"), "metadata": meta}}
    reason = str(meta.get("reason") or "")
    # Recovery blocks a task and releases it back to the queue in one
    # step. Nothing is waited from the reader, so the alarming half of the
    # pair is left out: the timeout and the release lines tell it all.
    transitional = etype == "task_blocked" and reason.lower().startswith("executor lost")
    if transitional and level != "advanced":
        return None
    actor = event.get("actor_id")
    task_id = event.get("task_id")
    kind, mark = _EVENT_MARKS.get(etype, ("normal", "circle"))
    item = {
        "seq": event.get("seq"), "ts": event.get("timestamp"),
        "title": text, "type": etype, "kind": kind, "icon": mark,
        "specialist": _actor_name(etype, actor, roles, meta, task_id),
        "task": (titles or {}).get(task_id),
    }
    # The engine uses one event type for two different situations. A write
    # outside the declared zone is not a clash between results.
    if etype == "workspace_conflict":
        files = meta.get("violations") or []
        if files:
            item["title"] = "Изменения вне разрешённой зоны"
            item["detail"] = "Файлы: " + ", ".join(str(f) for f in files[:5])
        elif meta.get("conflicts"):
            item["title"] = "Не удалось объединить результаты"
            item["detail"] = "Исходные версии сохранены."
        else:
            item["title"] = "Обнаружены противоречия при объединении"
    if transitional:
        item["title"] = "Исполнитель потерян (запись в журнале)"
        item["kind"], item["icon"] = "normal", "refresh"
        item["detail"] = "Задача сразу возвращена в очередь, решения человека не требуется."
        # A journal line that only repeats the timeout: nothing counts it.
        item["bookkeeping"] = True
    elif etype in ("task_blocked", "round_limit"):
        item["detail"] = _blocked_explanation(
            reason, failures=meta.get("failures"))[0]
        # The remark that exhausted the checks is written on this event,
        # not on a "failed" one: the reader must not have to look for it.
        if meta.get("feedback"):
            item["detail"] += ". Последнее замечание проверяющего: " + _remark(meta["feedback"])
    # The same event type can mean different things; metadata tells them
    # apart. A manual acceptance must never read as a passed check.
    if etype in ("verification_passed", "verify_pass") and meta.get("override"):
        item["title"] = "Результат принят вручную, без отчёта о проверке"
        item["detail"] = "Руководитель команды принял работу своим решением."
        item["kind"] = "manual"  # accepted, but not by a check: amber, not green
    if etype == "workspace_integrated" and meta.get("applied"):
        item["title"] = "Изменения внесены в проект"
        item["detail"] = "Объединённый результат применён, работа команды завершена."
    if etype == "agent_timeout" and quarantined:
        item["detail"] = ("Несохранённая работа исполнителя сохранена в архиве"
                          " рабочей области.")
    if etype == "task_released" and actor == "lead":
        item["detail"] = "Руководитель команды разрешил ещё одну попытку."
    if etype in ("verification_failed", "verify_fail") and meta.get("feedback"):
        item["detail"] = "Замечания проверяющего: " + _remark(meta["feedback"])
    if etype == "lead_token_rotated":
        item["detail"] = "Прежний ключ руководителя команды больше не действует."
    if level == "advanced":
        item["tech"] = {"event_id": event.get("event_id"), "run_id": event.get("run_id"),
                        "hash": event.get("hash"), "task_id": task_id,
                        "actor_id": actor, "metadata": meta}
    return item


def _blocked_explanation(reason: str, *, failures: int | None = None,
                         restarts: int | None = None) -> tuple[str, str, str, str]:
    """Why a task is blocked, in the reader's words.

    Returns (title, what happened, what the system tried, what to do).
    The reason is written by the engine and matched on its fixed part;
    the counts come from the task, not from the sentence, so the text
    stays true when the lead changes the limits. The fallback is honest
    about not knowing the exact cause.
    """
    text = (reason or "").lower()
    if "restart limit" in text or "timed out" in text:
        # The counter stops at the limit; the block comes on the timeout
        # after it.
        times = _times(restarts + 1 if restarts is not None else None)
        return (f"Специалист {times} переставал отвечать",
                f"Исполнитель задачи {times} не ответил в отведённое время, и система больше не перезапускает её сама.",
                "Каждый раз система возвращала задачу в очередь, чтобы исполнитель начал заново.",
                "Решите, кому продолжить эту задачу: разрешите ещё одну попытку либо снимите задачу.")
    if "verification" in text:
        times = _times(failures)
        if failures == 1:
            what = ("Исполнитель передал результат, и проверяющий нашёл недостатки."
                    " Новых попыток система сама не даёт.")
        else:
            what = (f"Исполнитель передавал результат {times}, и проверяющий"
                    " каждый раз находил недостатки.")
        return (f"Результат {times} не прошёл проверку", what,
                "Проверяющий каждый раз описывал, что доработать, и исполнитель получал эти замечания.",
                "Посмотрите замечания проверяющего, уточните требования и разрешите ещё одну попытку либо снимите задачу.")
    if "conflict" in text or "dependency" in text:
        return ("Результаты противоречат друг другу",
                "Задача зависит от результатов, которые нельзя объединить без противоречий.",
                "Система остановила задачу до начала работы, чтобы исполнитель не делал лишнего.",
                "Разрешите противоречие между результатами, от которых зависит эта задача.")
    return ("Задача остановлена",
            "Задача не может продолжаться без решения человека.",
            "Система остановила задачу и сохранила всю сделанную работу.",
            "Посмотрите состояние задачи в техническом режиме и решите, как продолжать.")


def attention_items(tasks: list[dict], run: dict | None, *,
                    now: datetime | None = None, threshold_s: float = 900.0,
                    remarks: dict | None = None,
                    budget: dict | None = None) -> list[dict]:
    """Everything that genuinely waits for a human decision.

    Each item answers the five questions of spec 4.5: what happened, why
    it matters, what the system tried, what the user has to do, and what
    happens if nothing is done. ``remarks`` maps a task id to the last
    remark of its checker.
    """
    now = now or datetime.now(timezone.utc)
    items: list[dict] = []
    for t in tasks:
        if t["status"] != "blocked":
            continue
        title, what, tried, action = _blocked_explanation(
            t.get("blocked_reason") or "",
            failures=t.get("verification_failures"), restarts=t.get("restarts"))
        items.append({
            "kind": "blocked", "task": t["id"], "title": t["title"],
            "icon": "alert",
            "what": what,
            "why": "Итоговый результат команды будет неполным, пока эта задача не закрыта.",
            "tried": tried,
            "action": action,
            "remarks": _remark((remarks or {}).get(t["id"])),
            "if_nothing": "Задача останется остановленной, и команда не сможет объединить результаты.",
            "since": t.get("blocked_at"),
        })
    if run and run.get("status") == "integration_failed":
        items.append({
            "kind": "integration", "task": None, "icon": "alert",
            "title": "Не удалось объединить результаты",
            "what": "При объединении результатов этапов обнаружены противоречия.",
            "why": "Без общего результата работа команды не может быть применена к проекту.",
            "tried": "Система сохранила все исходные версии и остановила объединение.",
            "action": "Разрешите противоречие между результатами и запустите объединение заново.",
            "remarks": "",
            "if_nothing": "Результаты останутся принятыми по отдельности, но не будут собраны вместе.",
            "since": run.get("finished_at"),
        })
    if run and run.get("status") == "apply_pending":
        items.append({
            "kind": "apply", "task": None, "icon": "check",
            "title": "Результат готов, изменения ещё не внесены",
            "what": "Команда закончила работу, общий результат проверен.",
            "why": "Пока изменения не применены, в проекте остаётся прежняя версия.",
            "tried": "Система проверила объединённый результат и ждёт подтверждения.",
            "action": "Подтвердите применение изменений к проекту.",
            "remarks": "",
            "if_nothing": "Результат останется сохранённым и готовым к применению в любой момент.",
            "since": run.get("finished_at"),
        })
    if budget and budget.get("state") == "limit":
        money = budget.get("spent_text", "не определена")
        items.append({
            "kind": "budget", "task": None, "title": "Бюджет израсходован",
            "icon": "alert",
            "what": f"Потрачено {money} из согласованного бюджета.",
            "why": "Новые задачи не запускаются: команда не закончит работу без новых расходов.",
            "tried": "Система не останавливала исполнителей, которые уже работали: их результаты будут доведены до конца.",
            "action": "Увеличьте бюджет, если работа должна продолжиться, либо завершите её с тем, что сделано.",
            "remarks": "",
            "if_nothing": "Оставшиеся задачи останутся в очереди, и команда не сможет объединить результаты.",
            "since": None,
        })
    for t in tasks:
        silent = _silent_s(t, now)
        if t["status"] == "in_progress" and silent is not None and silent > threshold_s:
            items.append({
                "kind": "silent", "task": t["id"], "title": t["title"],
                "icon": "hourglass",
                "what": "Специалист долго не выходит на связь.",
                "why": "Задача не двигается, пока исполнитель не отвечает.",
                "tried": "Система ждёт до истечения лимита времени, затем вернёт задачу в очередь.",
                "action": "Можно подождать либо передать задачу другому исполнителю.",
                "remarks": "",
                "if_nothing": "Задача будет перезапущена автоматически; сделанная, но не переданная работа не попадёт в результат.",
                "since": t.get("heartbeat_at"),
            })
    return items


def progress(tasks: list[dict]) -> dict:
    """Overall progress in whole tasks.

    Progress is counted over tasks, not over internal operations, so the
    number reflects the volume of user-visible work. Tasks carry equal
    weight: the engine has no size estimate, and a fake one would make
    the percentage look more precise than the data.
    """
    live = [t for t in tasks if t["status"] != "cancelled"]
    done = len([t for t in live if t["status"] == "completed"])
    active = len([t for t in live if t["status"] in ("in_progress", "awaiting_verification")])
    blocked = len([t for t in live if t["status"] == "blocked"])
    total = len(live)
    return {"done": done, "active": active, "needs_attention": blocked,
            "total": total, "percent": round(100 * done / total) if total else 0}


def cost_view(usage: list[dict], *, task_of: dict | None = None,
              role_of: dict | None = None) -> dict:
    """Spend in plain terms. An unknown cost is reported as unknown, never
    as zero money (spec 11.2).

    A cost stored with the record is the price of the moment the work
    was done; a record without one is estimated here. ``task_of`` and
    ``role_of`` name a usage record's task and specialist; with them the
    spend is also broken down by task and by role.
    """
    tokens = sum(u.get("total_tokens") or 0 for u in usage)
    known = 0.0
    unknown = 0
    by_task: dict[str, float] = {}
    by_role: dict[str, float] = {}
    # Every sum carries its own reliability: how many of its records had
    # no price (spec 11.1), so a sum is never read as the whole truth.
    unknown_task: dict[str, int] = {}
    unknown_role: dict[str, int] = {}
    for u in usage:
        cost, quality = cost_of(u)
        task = (task_of or {}).get(u.get("task_id"))
        role = (role_of or {}).get(u.get("agent_id"))
        if cost is None:
            unknown += 1
            if task:
                unknown_task[task] = unknown_task.get(task, 0) + 1
            if role:
                unknown_role[role] = unknown_role.get(role, 0) + 1
            continue
        known += cost
        if task:
            by_task[task] = by_task.get(task, 0.0) + cost
        if role:
            by_role[role] = by_role.get(role, 0.0) + cost
    out = {"tokens": tokens, "usd": round(known, 6), "unknown_records": unknown,
           "complete": unknown == 0, "quality": "estimated" if known else "unknown"}
    if task_of is not None or role_of is not None:
        out["by_task"] = {k: round(v, 6) for k, v in sorted(by_task.items())}
        out["by_role"] = {k: round(v, 6) for k, v in sorted(by_role.items())}
        out["unknown_by_task"] = unknown_task
        out["unknown_by_role"] = unknown_role
    return out


# ------------------------------------------------------ what the owner can do (17)

def actions_view(tasks: list[dict], *, run: dict | None = None, paused: bool = False,
                 budget_state: str = "none") -> list[dict]:
    """What the owner can do from the board right now (v2.1 spec 17).

    Read from the state only: an action is offered when the engine would
    accept it (the same edges the state machine allows). ``confirm`` is
    the sentence the owner agrees to first; empty when there is nothing
    to regret. ``ask`` names a number the owner has to give. The engine
    decides again when the action is done: this list is an offer, not a
    promise.
    """
    actions: list[dict] = []
    status = (run or {}).get("status", "active")
    if status == "active":
        actions.append({"key": "resume" if paused else "pause", "task": None,
                        "label": "Снять паузу" if paused else "Поставить на паузу",
                        "confirm": "" if paused else
                        "Новые задачи не будут начинаться, пока вы не снимете паузу. "
                        "Те, что уже в работе, продолжат."})
    for t in tasks:
        title, state = t["title"], t["status"]
        if state == "blocked":
            actions.append({"key": "retry", "task": t["id"], "confirm": "",
                            "label": "Разрешить ещё одну попытку: " + title})
        if state in ("pending", "in_progress", "awaiting_verification") and status == "active":
            actions.append({"key": "cancel", "task": t["id"],
                            "label": "Отменить этап: " + title,
                            "confirm": ("Этап в работе. Его закроют, специалист "
                                        "освободится, а сделанное сохранится в архиве, "
                                        "но не войдёт в итог."
                                        if state != "pending" else
                                        "Этап закроют, и он не войдёт в итог.")})
        if state == "awaiting_verification":
            actions.append({"key": "request_check", "task": t["id"], "confirm": "",
                            "label": "Попросить проверить: " + title})
    live = [t for t in tasks if t["status"] != "cancelled"]
    accepted = [t for t in live if t["status"] == "completed"]
    if status == "active" and live and len(accepted) == len(live):
        actions.append({"key": "integrate", "task": None, "label": "Объединить результаты",
                        "confirm": "Отдельные результаты соберутся в один, и он пройдёт "
                                   "итоговую проверку. Если найдётся противоречие, исходные "
                                   "версии останутся на месте."})
    elif status == "integration_failed":
        actions.append({"key": "integrate", "task": None, "label": "Запустить объединение заново",
                        "confirm": "Объединение начнётся заново. Если противоречие не устранено, "
                                   "оно снова остановится, и исходные версии останутся на месте."})
    if status == "apply_pending":
        actions.append({"key": "apply", "task": None, "label": "Применить изменения к проекту",
                        "confirm": "Собранный результат будет внесён в проект. Отменить это "
                                   "через доску нельзя."})
    if budget_state == "limit":
        actions.append({"key": "budget", "task": None, "label": "Увеличить бюджет",
                        "ask": "Новый бюджет команды, в долларах", "confirm": ""})
    return actions

