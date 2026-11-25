"""What the numbers of a team mean for its owner (v2.1 spec 10 to 13).

Pure functions over what the snapshot already read: the tasks, the
checks of each task, the hand-overs, the journal and the spend. Three
rules run through all of it:

- an automatic check of files and commands and a judgement of content
  are different things and are never summed into one number;
- a result the lead accepted by decision is neither of them;
- a number the data does not support is not shown, and a cost that is
  not known is said to be not known, never shown as zero.
"""
from __future__ import annotations

from .observability import cost_of, price_per_1m
from .viewmodel import parse_time

MACHINE_TYPES = ("test", "file_check", "security_check")
# The states of a team against its budget, decided from the same rule as
# the engine's (engine.op_budget).
BUDGET_STATE_TEXT = {
    "none": "Бюджет не согласован: расходы считаются, предел не установлен.",
    "ok": "Расходы в пределах бюджета.",
    "warning": "Бюджет почти израсходован: новых задач может не хватить.",
    "limit": "Бюджет израсходован. Новые задачи не запускаются, пока руководитель "
             "команды не увеличит предел. Уже работающие исполнители не останавливаются.",
}


def _money(value: float | None) -> str:
    if value is None:
        return "не согласован"
    return f"${value:,.2f}" if value >= 0.01 or value == 0 else f"${value:.6f}"


# ---------------------------------------------------------------- quality (10)

def quality_view(tasks: list[dict], attempts: list[dict],
                 accepted_by_lead: set[str], titles: dict | None = None) -> dict:
    """How far the results of a team can be trusted (v2.1 spec 10).

    Three things are counted apart, because they are different:

    - what the machine checked (commands, files, a security scanner):
      from the criterion results the engine stored with each check;
    - what a checker judged about the content: the verdicts;
    - what the lead accepted by decision, without a check.

    A check that passed on a task the lead accepted by decision was not
    a check: it is left out of the counts of checks.

    ``titles`` names a task by its title in what is shown; without it
    the identifier stands in.
    """
    checks_by_task: dict[str, list[dict]] = {}
    for a in attempts:
        checks_by_task.setdefault(a["task_id"], []).append(a)

    handed = [t for t in tasks if checks_by_task.get(t["id"]) or t.get("submitted_at")]
    by_decision = [t for t in handed if t["id"] in accepted_by_lead]
    # Judged, and already checked: a result handed over whose check has
    # not come back yet is neither passed nor failed.
    judged = [t for t in handed
              if t["id"] not in accepted_by_lead and checks_by_task.get(t["id"])]
    first_time = [t for t in judged
                  if checks_by_task[t["id"]][0].get("verdict") == "pass"]
    reworked = [t for t in judged
                if any(a.get("verdict") == "fail" for a in checks_by_task[t["id"]])]
    checks = [a for t in judged for a in checks_by_task[t["id"]]]
    passed = len([a for a in checks if a.get("verdict") == "pass"])
    failed = len([a for a in checks if a.get("verdict") == "fail"])

    # What the machine checked, from the latest check of each judged task.
    # One result, one bucket: how it got as far as it did (spec 10.2).
    # A result that passed at first and was then sent back is one that
    # needed rework, like any other.
    clean = {t["id"] for t in first_time
             if not any(a.get("verdict") == "fail" for a in checks_by_task[t["id"]])}
    split = {"first": len(clean), "reworked": len(judged) - len(clean),
             "decision": len(by_decision),
             "awaiting": max(0, len(handed) - len(judged) - len(by_decision))}
    # Every check in the order the answers came back: the strip of marks
    # shows how the checks went, and where one result was sent back more
    # than once.
    timeline = [{"ts": a.get("created_at"), "verdict": a.get("verdict"),
                 "attempt": a.get("attempt"),
                 "task": (titles or {}).get(a["task_id"], a["task_id"])}
                for a in sorted(checks, key=lambda a: a.get("created_at") or "")]

    machine = {"run": 0, "passed": 0, "failed": 0, "forced": 0}
    for t in judged:
        latest = checks_by_task[t["id"]][-1]
        report = latest.get("report") if isinstance(latest.get("report"), dict) else {}
        for criterion in report.get("criteria") or []:
            if not isinstance(criterion, dict):
                continue
            machine["run"] += 1
            machine["passed" if criterion.get("passed") else "failed"] += 1
        machine["forced"] += len(report.get("forced_failures") or [])

    problems = [{"task": (titles or {}).get(a["task_id"], a["task_id"]),
                 "remark": a.get("feedback") or "",
                 "when": a.get("created_at"), "attempt": a.get("attempt")}
                for a in sorted(checks, key=lambda a: a.get("created_at") or "", reverse=True)
                if a.get("verdict") == "fail"]
    awaited = [t for t in tasks if t["status"] in ("in_progress", "awaiting_verification")]

    total = len(judged)
    if not handed:
        story = "Результаты ещё не передавались на проверку."
    elif total == 0:
        story = "Результаты переданы на проверку, её ответ ещё не пришёл."
    elif total == 1:
        story = ("1 результат прошёл проверку с первого раза." if first_time
                 else "Проверенный результат не прошёл проверку с первого раза.")
    elif len(first_time) == total:
        story = (f"Все {total} "
                 + _one_of(total, "результат прошёл", "результата прошли", "результатов прошли")
                 + " проверку с первого раза.")
    else:
        story = (f"{len(first_time)} из {total} результатов прошли проверку с первого раза."
                 + (f" Ещё {len(reworked)} "
                    + _one_of(len(reworked), "результат пришлось переделать",
                              "результата пришлось переделать", "результатов пришлось переделать") + "."
                    if reworked else ""))
    if handed and by_decision:
        story += (f" {len(by_decision)} "
                  + _one_of(len(by_decision), "результат принят", "результата приняты",
                            "результатов приняты")
                  + " решением руководителя команды, без проверки.")
    return {
        "handed_over": len(handed), "checks": len(checks),
        "passed": passed, "failed": failed,
        "accepted_by_check": len([t for t in judged if t["status"] == "completed"]),
        "accepted_by_decision": len(by_decision),
        "first_time": len(first_time), "reworked": len(reworked), "judged": total,
        "split": split, "timeline": timeline,
        "machine": machine,
        "awaited": [t["title"] for t in awaited],
        "problems": problems,
        "story": story,
    }


def _one_of(n: int, one: str, few: str, many: str) -> str:
    last, tenth = n % 10, n % 100
    if last == 1 and tenth != 11:
        return one
    if last in (2, 3, 4) and tenth not in (12, 13, 14):
        return few
    return many


# ------------------------------------------------------------------ budget (11)

def spend_series(usage: list[dict]) -> list[dict]:
    """The spend as it grew, record by record: ``[{"ts", "usd"}]`` with
    the running total (v2.1 spec 11.1, the dynamics of the spend). Only
    records with a price are plotted; the others are not a zero."""
    series, total = [], 0.0
    for record in sorted((r for r in usage if r.get("cost_usd") is not None),
                         key=lambda r: (r.get("recorded_at") or "", r.get("agent_id") or "")):
        total += record["cost_usd"]
        series.append({"ts": record.get("recorded_at"), "usd": round(total, 6)})
    return series


def budget_view(cost: dict, config: dict, tasks: list[dict]) -> dict:
    """Spend against the agreed budget (v2.1 spec 11.2).

    The state is decided by the rule the engine enforces (the same one
    as ``engine.op_budget``): a budget that is exactly spent is spent.
    The engine watches the limit over the spend it knows; records
    without a price are named here, so the owner sees the limit's reach.
    """
    budget = config.get("budget_usd")
    per_task = config.get("budget_per_task_usd")
    ratio = config.get("budget_warn_ratio", 0.8)
    spent = cost.get("usd", 0.0)
    unknown = cost.get("unknown_records", 0)
    state = "none"
    if budget is not None:
        state = ("limit" if spent >= round(budget, 6)
                 else "warning" if spent >= round(ratio * budget, 6) else "ok")

    by_task = cost.get("by_task") or {}
    over_limit = sorted(title for title, value in by_task.items()
                        if per_task is not None and value >= round(per_task, 6))
    worked = [t for t in tasks if t.get("claimed_at")]
    accepted = [t for t in tasks if t["status"] == "completed"]
    usd_per_task = round(spent / len(worked), 6) if worked and spent else None
    usd_per_result = round(spent / len(accepted), 6) if accepted and spent else None

    if state == "none":
        story = (f"Потрачено {_money(spent)}." if spent or unknown
                 else "Расходов пока нет.") + " " + BUDGET_STATE_TEXT["none"]
        if unknown:
            story += f" Цена {unknown} записей об использовании неизвестна и в сумму не входит."
    else:
        left = max(0.0, round(budget - spent, 6))
        story = f"На проект выделено {_money(budget)}. Потрачено {_money(spent)}. Осталось {_money(left)}."
        if state != "ok":
            story += " " + BUDGET_STATE_TEXT[state]
        if unknown:
            story += " Предел считается по известным расходам: цена части записей неизвестна."
    return {
        "budget_usd": budget, "per_task_usd": per_task, "warn_ratio": ratio,
        "spent_usd": spent, "remaining_usd": (max(0.0, round(budget - spent, 6))
                                              if budget is not None else None),
        "spent_text": _money(spent) if (spent or not unknown) else "не определена",
        "unknown_records": unknown,
        "complete": cost.get("complete", True),
        "state": state, "share": round(spent / budget, 4) if budget else None,
        "over_limit": over_limit,
        "by_task": by_task, "by_role": cost.get("by_role") or {},
        "unknown_by_task": cost.get("unknown_by_task") or {},
        "unknown_by_role": cost.get("unknown_by_role") or {},
        "usd_per_task": usd_per_task, "usd_per_result": usd_per_result,
        "per_task_text": _money(usd_per_task) if usd_per_task is not None else "не определена",
        "per_result_text": (_money(usd_per_result) if usd_per_result is not None
                            else "не определена"),
        "story": story,
    }


def model_rows(usage: list[dict]) -> list[dict]:
    """Tokens and money per model, for the advanced level (v2.1 spec 11.3).

    One row per provider and model, in the order the models first appear
    in the records. The price is the row of the table the cost was priced
    by; a record without a price is counted, never priced at zero.
    """
    rows: dict[tuple, dict] = {}
    for record in usage:
        key = (record.get("provider") or "не указан", record.get("model") or "не указана")
        row = rows.get(key)
        if row is None:
            row = rows[key] = {"provider": key[0], "model": key[1], "records": 0,
                               "input_tokens": 0, "output_tokens": 0,
                               "cache_read_tokens": 0, "cache_write_tokens": 0,
                               "usd": 0.0, "unknown": 0, "quality": [],
                               "price_per_1m": price_per_1m(key[1])}
        row["records"] += 1
        for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            row[field] += record.get(field) or 0
        cost, quality = cost_of(record)
        if cost is None:
            row["unknown"] += 1
            continue
        row["usd"] += cost
        if quality not in row["quality"]:
            row["quality"].append(quality)
    for row in rows.values():
        row["usd"] = round(row["usd"], 6)
    return list(rows.values())


# ------------------------------------------------------- problems and recovery (12)

# The events of the journal that are problems, and what each is about.
_PROBLEMS = {
    "agent_timeout": ("executor", "Исполнитель перестал отвечать"),
    "agent_failed": ("executor", "Исполнитель перестал отвечать"),
    "verification_failed": ("check", "Результат не прошёл проверку"),
    "verify_fail": ("check", "Результат не прошёл проверку"),
    "workspace_conflict": ("contradiction", "Обнаружены противоречия"),
    "task_blocked": ("blocked", "Задача остановлена"),
    "round_limit": ("blocked", "Задача остановлена"),
}
PROBLEM_EVENTS = frozenset(_PROBLEMS)
_PROBLEM_TEXT = {
    "executor": "Исполнитель перестал отвечать",
    "check": "Результат не прошёл проверку",
    "contradiction": "Обнаружены противоречия",
    "blocked": "Задача остановлена",
    "budget": "Бюджет исчерпан",
}


def recovery_view(tasks: list[dict], events_by_task: dict[str, list[dict]],
                  budget: dict | None = None) -> dict:
    """Every problem the team ran into and how it ended (v2.1 spec 12).

    Read from the journal only. A problem is not hidden because it
    repeated, and a recovery that did not work is not shown as one that
    did: the outcome is the state of the task now. ``budget`` is the
    team's view against its budget: a spent budget is a problem too, the
    one the engine answers every new start with.
    """
    by_id = {t["id"]: t for t in tasks}
    incidents: list[dict] = []
    for task_id, events in events_by_task.items():
        task = by_id.get(task_id)
        if task is None:
            continue
        after: dict[int, list[dict]] = {}
        for index, item in enumerate(events):
            after.setdefault(item["seq"], []).extend(events[index + 1:index + 4])
        for item in events:
            kind = _PROBLEMS.get(item.get("type"))
            if not kind:
                continue
            # The block recovery writes and lifts in one step is no
            # problem of the owner (viewmodel.event_view hides it in the
            # simple level; here it is told as the timeout it belongs to).
            if item["type"] == "task_blocked" and any(
                    earlier.get("type") == "agent_timeout" for earlier in events
                    if earlier["seq"] < item["seq"]):
                continue
            what_followed = after.get(item["seq"], [])
            did = []
            # What the owner cannot lose is said out loud (spec 12).
            saved = None
            if task.get("metadata", {}).get("quarantine_dir"):
                saved = "рабочая область прежней попытки лежит в архиве"
            elif kind[0] == "check":
                saved = "сданная версия результата и замечания сохранены"
            elif kind[0] == "blocked":
                saved = "вся сделанная работа сохранена"
            elif kind[0] == "contradiction":
                saved = "исходные результаты сохранены"
            if any(e["type"] == "task_released" for e in what_followed):
                did.append("вернула задачу в очередь")
            if task.get("metadata", {}).get("quarantine_dir"):
                did.append("сохранила рабочую область исполнителя в архиве")
            if kind[0] == "check":
                did.append("передала замечания проверяющего исполнителю")
            if kind[0] == "blocked":
                did.append("остановила задачу и сохранила всю сделанную работу")
            if kind[0] == "contradiction":
                did.append("остановила работу до начала, исходные результаты сохранены")
            # What came of it is read from what followed at once, not from
            # the state of the task now: a result that was reworked and
            # handed in again was dealt with, whatever happened later.
            later_types = [e["type"] for e in events if e["seq"] > item["seq"]]
            if kind[0] == "check" and any(t in ("task_submitted", "verification_passed",
                                                "task_completed") for t in later_types):
                outcome, human_needed, risk = \
                    "исполнитель доработал результат и передал его снова", False, False
            elif kind[0] == "executor" and "task_claimed" in later_types:
                # Who continued the work is the executor who claimed it
                # next, by name.
                claim = next(e for e in events
                             if e["seq"] > item["seq"] and e["type"] == "task_claimed")
                continued_by = claim.get("specialist") or None
                outcome = ("задачу продолжил " + continued_by if continued_by
                           else "задача запущена заново")
                human_needed, risk = False, False
            else:
                status = task["status"]
                human_needed = status == "blocked"
                if status == "completed":
                    outcome, risk = "результат принят", False
                elif human_needed:
                    outcome, risk = "нужна помощь: задача остановлена", \
                        bool(task.get("restarts") or task.get("verification_failures"))
                elif status in ("in_progress", "awaiting_verification"):
                    outcome, risk = "работа продолжается", False
                else:
                    outcome, risk = "задача ожидает в очереди", False
            incidents.append({
                "task": task["title"], "kind": kind[0], "title": kind[1],
                "when": item.get("ts"), "detail": item.get("detail", ""),
                "affected": task["title"],
                "did": did or ["зафиксировала событие"],
                "saved": saved,
                "risk_of_loss": risk,
                "human_needed": human_needed,
                "action": ("Разрешите ещё одну попытку либо снимите задачу." if human_needed
                           else "Ничего не требуется: система продолжает сама."),
                "outcome": outcome,
            })
    if budget is not None and budget.get("state") == "limit":
        # Not in the journal: the engine refuses the start and writes
        # nothing. It is still the problem the owner has to answer.
        incidents.append({
            "task": None, "kind": "budget", "title": _PROBLEM_TEXT["budget"],
            "when": None, "detail": "Новые задачи не запускаются.",
            "affected": "запуск новых задач",
            "did": ["остановила запуск новых задач",
                    "не трогает исполнителей, которые уже работают"],
            "saved": "все сделанные результаты сохранены",
            "risk_of_loss": False, "human_needed": True,
            "action": "Увеличьте бюджет, чтобы команда продолжила, "
                      "или завершите работу с тем, что готово.",
            "outcome": "нужна помощь: без нового бюджета работа не продолжится",
        })
    # What waits for the owner comes first, the newest of it on top.
    incidents.sort(key=lambda i: (i["human_needed"], i["when"] or ""), reverse=True)
    by_kind: dict[str, int] = {}
    for incident in incidents:
        by_kind[incident["kind"]] = by_kind.get(incident["kind"], 0) + 1
    repeated: dict[str, int] = {}
    for incident in incidents:
        repeated[incident["task"]] = repeated.get(incident["task"], 0) + 1
    open_now = [i for i in incidents if i["human_needed"]]
    resolved = [i for i in incidents if not i["human_needed"]]
    if not incidents:
        story = "Проблем не было: ни один исполнитель не терял связь, ни одна проверка не вернула результат."
    elif open_now:
        waiting = {i["task"] for i in open_now if i["task"]}
        parts = [str(len(waiting)) + " "
                 + _one_of(len(waiting), "задаче", "задачам", "задачам")] if waiting else []
        if any(i["kind"] == "budget" for i in open_now):
            parts.append("бюджету")
        story = f"Было проблем: {len(incidents)}. Сейчас нужна ваша помощь по " + " и ".join(parts) + "."
    else:
        story = (f"Было проблем: {len(incidents)}. Ни одна не ждёт вашего решения."
                 if resolved else f"Было проблем: {len(incidents)}.")
    return {
        "total": len(incidents), "by_kind": by_kind,
        "kinds": [{"kind": kind, "title": _PROBLEM_TEXT[kind], "count": by_kind[kind]}
                  for kind in ("budget", "executor", "check", "contradiction", "blocked")
                  if kind in by_kind],
        "repeated": {task: n for task, n in repeated.items() if n > 1},
        "open": len(open_now), "resolved": len(resolved),
        "incidents": incidents, "story": story,
    }


# ------------------------------------------------------------ integration (13)

def integration_view(tasks: list[dict], run: dict | None, events: list[dict]) -> dict:
    """How the parts become the whole result (v2.1 spec 13).

    Three states are kept apart: a result accepted, the results
    integrated, the changes applied to the project. The run's status is
    the engine's own record of the last two.
    """
    live = [t for t in tasks if t["status"] != "cancelled"]
    accepted = [t for t in live if t["status"] == "completed"]
    status = (run or {}).get("status", "active")
    # A clash between results, as the view model words it (a write
    # outside the allowed zone has the same event type and other words).
    conflict = next((e for e in reversed(events) if e["type"] == "workspace_conflict"
                     and (str(e.get("title") or "").startswith("Не удалось объединить")
                          or "объединить" in str(e.get("detail") or ""))), None)
    if status == "finished":
        integrated = applied = True
    elif status == "apply_pending":
        integrated, applied = True, False
    elif status == "integration_failed":
        integrated = applied = False
    else:
        integrated = applied = False
    if not live:
        state, story = "empty", "В плане пока нет задач."
    elif applied:
        state, story = "applied", "Финальная версия готова: изменения внесены в проект."
    elif integrated:
        state, story = "ready", ("Отдельные результаты объединены в один и проверены. "
                                 "Осталось подтвердить применение изменений к проекту.")
    elif status == "integration_failed":
        state = "conflict"
        story = ("Не удалось объединить результаты. Исходные версии сохранены. "
                 "Противоречие нужно разрешить и запустить объединение заново.")
    elif len(accepted) == len(live):
        state, story = "collecting", "Все результаты приняты. Можно запускать объединение."
    else:
        state = "collecting"
        story = (f"Принято {len(accepted)} из {len(live)} результатов. "
                 "Объединение начнётся, когда будут приняты все.")
    return {
        "state": state, "story": story,
        "accepted": len(accepted), "total": len(live),
        "steps": [
            {"key": "accepted", "title": "Результаты приняты", "done": len(accepted) == len(live) and bool(live),
             "text": f"{len(accepted)} из {len(live)}"},
            {"key": "integrated", "title": "Результаты объединены", "done": integrated,
             "text": "да" if integrated else "ещё нет"},
            {"key": "applied", "title": "Изменения применены к проекту", "done": applied,
             "text": "да" if applied else "ещё нет"},
        ],
        "conflict": {"title": conflict["title"], "detail": conflict.get("detail", ""),
                     "when": conflict.get("ts")} if conflict else None,
    }


# ---------------------------------------------------------------- benchmark (19)

def benchmark_compare(name: str, teams: list[dict], this_team: str) -> dict:
    """Compare teams that ran one declared set of tasks (v2.1 spec 19).

    A team declares the benchmark it runs in its config
    (``benchmark: {"name", "executors"}``). Only teams with the same
    name are comparable, and a sentence about speed or money is made
    only from teams that finished. Without two of them the answer is
    that there is nothing to compare: no estimate stands in for a
    measurement.

    Each row of ``teams`` is ``{"team", "executors", "finished",
    "duration_s", "spent_usd", "cost_complete", "accepted", "total",
    "first_time", "judged"}``.
    """
    rows = sorted((t for t in teams if t.get("executors")), key=lambda t: t["executors"])
    done = [t for t in rows if t.get("finished") and (t.get("duration_s") or 0) > 0]
    out = {"name": name, "rows": rows, "statement": None, "note": None}
    if not rows:
        out["note"] = "Ни одна команда этого набора пока не записана."
        return out
    if len(done) < 2:
        out["note"] = ("Сопоставимых измерений пока нет: нужно, чтобы завершились не меньше "
                       "двух команд с одним набором задач. Оценки вместо измерений не показываются.")
        return out
    one = done[0]
    mine = next((t for t in done if t["team"] == this_team), done[-1])
    if mine is one:
        out["note"] = "Это самая маленькая завершённая команда набора: с ней сравнивают остальные."
        return out
    faster = 1 - mine["duration_s"] / one["duration_s"]
    speed = ("за то же время" if abs(faster) < 0.02 else
             f"на {round(abs(faster) * 100)}% {'быстрее' if faster > 0 else 'медленнее'}")
    executors = f"{one['executors']} " + _one_of(one["executors"], "исполнителя", "исполнителей",
                                                   "исполнителей")
    sentence = (f"Команда из {mine['executors']} специалистов выполнила набор задач {speed}, "
                f"чем команда из {executors}")
    if mine.get("cost_complete") and one.get("cost_complete") and one.get("spent_usd"):
        change = mine["spent_usd"] / one["spent_usd"] - 1
        money = ("затраты не изменились" if abs(change) < 0.02 else
                 f"затраты {'увеличились' if change > 0 else 'снизились'} на {round(abs(change) * 100)}%")
        sentence += f", при этом {money}"
    else:
        sentence += "; затраты не сравнить: цена части записей неизвестна"
    if mine.get("judged") and one.get("judged"):
        sentence += (f". С первого раза прошло {mine['first_time']} из {mine['judged']} "
                     f"результатов против {one['first_time']} из {one['judged']}")
    out["statement"] = sentence + "."
    return out
