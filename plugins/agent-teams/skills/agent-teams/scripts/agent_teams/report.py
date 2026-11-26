"""HTML report of the team's work for a manager (v2.1 spec, section 18).

One self-contained file: a ``<style>`` element in the head, no network,
no script, openable long after the engine has finished. Twelve sections
in the order of the specification, each an element with a stable id. The
technical details live in one ``<details>`` element that is closed by
default, and nothing technical (an identifier, a commit, a branch, a
path of the team's directory) stands before it.

Everything is read from the read-only snapshot the board is built from
(``dashboard_data.snapshot``, advanced level), so the report says what
the board says: the same words, the same states, the same numbers. A
figure is never counted here that ``analytics`` already counts, and none
is invented. Three rules of the board run through every section: a
machine check, the judgement of the checker and a decision of the lead
are different things and are never summed; a cost that is not known is
"не определена", never zero; a result the lead accepted by decision is
never shown as a passed check.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from . import dashboard_data, viewmodel
from .analytics import BUDGET_STATE_TEXT, _money, _one_of
from .errors import NotFoundError
from .storage import Store
from .viewmodel import parse_time

# The twelve sections of the specification (18), in their order: the id
# of the element and the words of its heading. The last one is the
# collapsible block of technical details.
SECTIONS = (
    ("sec-goal", "Цель проекта"),
    ("sec-result", "Итоговый результат"),
    ("sec-done", "Что выполнено"),
    ("sec-not-done", "Что не удалось выполнить"),
    ("sec-problems", "Какие проблемы возникали"),
    ("sec-how-solved", "Как они решались"),
    ("sec-quality", "Качество результатов"),
    ("sec-cost", "Расходы"),
    ("sec-time", "Время выполнения"),
    ("sec-team", "Состав команды и вклад специалистов"),
    ("sec-history", "История этапов"),
    ("sec-tech", "Технические подробности"),
)

# The feed of the snapshot is a window of the newest events (200 by
# default: enough for a board that polls). A report is written once and
# has to carry the whole journal, so the window is asked as wide as a
# journal can be and the limits of a polling page are lifted
# (``whole=True``: every incident, every mark, every raw record). One
# call that also asks for ``task_events`` gives one consistent snapshot:
# the history of every stage is read together with everything else.
_WHOLE_JOURNAL = 1_000_000

# A tone is always a class of this page, never data: a state is read
# from its words, the colour only repeats them.
_KINDS = frozenset({"idle", "busy", "attention", "done", "problem"})
_AGENT_TONE = {"working": "busy", "verifying": "busy", "waiting": "attention",
               "recovering": "attention", "problem": "problem", "done": "done",
               "free": "idle"}
_AGENT_TONE_BY_LABEL = {viewmodel.AGENT_STATE[key]["label"]: tone
                        for key, tone in _AGENT_TONE.items()}
_SPAN_TONE = {"work": "work", "check": "check", "fix": "fix", "wait": "wait",
              "recovery": "recovery", "done": "done", "stopped": "stopped"}
# The words of the board for the quality of a price.
_QUALITY_TEXT = {"estimated": "оценка по таблице цен", "stored": "цена на момент работы",
                 "unknown": "неизвестно"}
# The three checkpoints the engine records, and what the board calls them.
_CHECKPOINTS = (("started", "Начата"), ("submitted", "Передана на проверку"),
                ("accepted", "Принята"))


def _esc(value) -> str:
    """Every value that comes from the data passes through here."""
    return html.escape(str(value if value is not None else ""))


def _n(value) -> str:
    """A count from the data as text. A value that is not a whole number
    never reaches the page as markup: it stops the render instead."""
    return str(int(value))


def _list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _grouped(value) -> str:
    """A large number with a space every three digits, as on the board."""
    return f"{int(value):,}".replace(",", " ")


def _dur(seconds) -> str:
    """A length of time in the words of the board (45 с, 12 мин,
    1 ч 5 мин), and in days once there are any."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} с"
    minutes = total // 60
    if total < 3600:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч" + (f" {minutes} мин" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days} дн" + (f" {hours} ч" if hours else "")


def _local(value, tz=None) -> datetime | None:
    """An ISO timestamp of the engine in the zone of the one who reads
    (the local zone of the machine that writes the report), or None."""
    parsed = parse_time(value)
    return None if parsed is None else parsed.astimezone(tz)


def _fmt(value, pattern: str, tz=None) -> str:
    moment = _local(value, tz)
    return "" if moment is None else moment.strftime(pattern)


def _when(value, tz=None) -> str:
    """25.11.2026 21:29, or an honest sentence when nothing was recorded."""
    return _fmt(value, "%d.%m.%Y %H:%M", tz) or "время не записано"


def _zone(value, tz=None) -> str:
    """The zone every time of the report is shown in (UTC+03:00, UTC)."""
    moment = _local(value, tz)
    if moment is None:
        return ""
    offset = moment.strftime("%z")
    if offset in ("", "+0000", "-0000"):
        return "UTC"
    return f"UTC{offset[:3]}:{offset[3:]}"


def _seconds(start, end) -> float | None:
    """From one recorded moment to the next; None when either is unreadable."""
    began, finished = _local(start), _local(end)
    if began is None or finished is None:
        return None
    return max(0.0, (finished - began).total_seconds())


def _reliability(unknown: int) -> str:
    """How far a sum can be trusted, said under every total."""
    if not unknown:
        return "Все расходы известны."
    records = "записи" if unknown == 1 else "записей"
    counted = "она не учтена" if unknown == 1 else "они не учтены"
    return f"Цена {unknown} {records} неизвестна: {counted}."


def _money_rows(sums: dict, unknown: dict) -> list[tuple[str, str, str]]:
    """The lines of one list of sums (by stage, by specialist), the way
    the board makes them: a sum some of whose records have no price keeps
    its known part and says so; one with no priced record at all is "not
    defined", never a zero."""
    sums, unknown = sums or {}, unknown or {}
    rows = []
    for name in list(sums) + [n for n in unknown if n not in sums]:
        value = float(sums.get(name) or 0)
        missing = int(unknown.get(name) or 0)
        note = (f"и ещё цена {missing} "
                + _one_of(missing, "записи", "записей", "записей") + " неизвестна"
                if missing else "")
        rows.append((name, _esc(_money(value)) if value else "не определена", note))
    return rows


def _money_list(sums: dict, unknown: dict) -> str:
    rows = _money_rows(sums, unknown)
    if not rows:
        return '<p class="muted">Данных пока нет.</p>'
    return '<ul class="sums">' + "".join(
        f'<li><span class="name">{_esc(name)}</span><span class="value">{text}</span>'
        + (f'<span class="note">{_esc(note)}</span>' if note else "") + "</li>"
        for name, text, note in rows) + "</ul>"


def _badge(state, tone) -> str:
    """A state as a badge. Its tone is a fixed class, never data."""
    css = tone if tone in _KINDS else "idle"
    return f'<span class="state {css}">{_esc(state)}</span>'


def _code(value) -> str:
    return f"<code>{_esc(value)}</code>" if value not in (None, "") else "-"


def _codes(values, empty: str) -> str:
    return ", ".join(_code(value) for value in _list(values)) or empty


def _dl(rows) -> str:
    """A list of facts. ``rows`` are (term, value) pairs; a value is
    markup the caller built from escaped data, an empty one is left out."""
    return ('<dl class="facts">'
            + "".join(f"<dt>{_esc(term)}</dt><dd>{value}</dd>" for term, value in rows if value)
            + "</dl>")


def _table(head: tuple, rows: list, css: str = "") -> str:
    """A table whose cells the caller already escaped."""
    kind = f' class="{css}"' if css else ""
    out = (f"<table{kind}><thead><tr>"
           + "".join(f"<th>{_esc(name)}</th>" for name in head)
           + "</tr></thead><tbody>")
    for row in rows:
        out += "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
    return out + "</tbody></table>"


def _stats(rows) -> str:
    """A table of figures: (label, value, what it counts)."""
    return '<table class="stats">' + "".join(
        f'<tr><th scope="row">{_esc(label)}'
        + (f'<small>{_esc(note)}</small>' if note else "")
        + f"</th><td>{value}</td></tr>"
        for label, value, note in rows) + "</table>"


# ------------------------------------------------------- what a stage says


def _checks(task: dict) -> str:
    """The checks of one stage in the order their answers came back, in
    the words of the board. A check that stands behind a decision of the
    lead is marked so by the snapshot and is named a decision here."""
    checks = _list(task.get("checks"))
    if not checks:
        return '<p class="muted">Результат ещё не передавался на проверку.</p>'
    lines = []
    for check in checks:
        if check.get("passed"):
            verdict, tone = (("Принято вручную, без проверки", "manual") if check.get("manual")
                             else ("Проверка пройдена", "done"))
        else:
            verdict, tone = "Проверка не пройдена", "problem"
        remark = _esc(check.get("feedback"))
        lines.append(f'<li class="{tone}">{verdict}' + (f": {remark}" if remark else "")
                     + "</li>")
    return '<ol class="checks">' + "".join(lines) + "</ol>"


def _result(task: dict) -> str:
    """What a stage produced, as the board words it: the accepted version
    once the stage is accepted, otherwise the last handed-over version."""
    result = task.get("result") or {}
    if not result.get("handed_over"):
        return "Результат ещё не передавался."
    ready = bool(result.get("ready"))
    text = ("Принятая версия результата." if ready
            else "Промежуточный результат, ещё не принят.")
    times = int(result.get("handed_over_count") or 0)
    if times > 1:
        text += (f" Результат передавался {times} "
                 + _one_of(times, "раз", "раза", "раз") + ", показана последняя версия.")
    files = _codes(result.get("files"), "")
    if not files:
        return text + " Результат передан, но файлов в нём нет: исполнитель ничего не подготовил."
    return text + " Файлы: " + files + "."


def _title_line(task: dict) -> str:
    """The name of a stage with its state, as every list of stages opens."""
    hint = _esc(task.get("state_hint"))
    return (f'<p class="tt"><b>{_esc(task.get("title"))}</b> '
            f"{_badge(task.get('state'), task.get('kind'))}"
            + (f' <span class="muted small">{hint}</span>' if hint else "") + "</p>")


def _done_so_far(task: dict) -> str:
    points = task.get("checkpoints") or {}
    reached = [word for key, word in _CHECKPOINTS if points.get(key)]
    return ", ".join(reached) if reached else "ещё не начата"


def _open_stage(task: dict, attention: dict) -> list:
    """The facts of a stage that is not accepted: its next step, and what
    waits for the owner when something does."""
    rows = [("Исполнитель", _esc(task.get("role"))),
            ("Дальше", _esc(task.get("next_step"))),
            ("Ожидаемый результат", _codes(task.get("expected"), "файлы не указаны")),
            ("Что уже сделано", _esc(_done_so_far(task)))]
    if (task.get("result") or {}).get("handed_over"):
        rows.append(("Результат", _result(task)))
    if _list(task.get("checks")):
        rows.append(("Проверка результата", _checks(task)))
    item = attention.get(task.get("id"))
    if item:
        # What waits for the owner, in the words of the attention card.
        for term, key in (("Замечания проверяющего", "remarks"), ("Что произошло", "what"),
                          ("Почему это важно", "why"), ("Что система сделала", "tried"),
                          ("Что нужно от вас", "action"),
                          ("Если ничего не делать", "if_nothing")):
            if item.get(key):
                rows.append((term, _esc(item[key])))
    return rows


# ------------------------------------------------------------ the sections


def _sec_goal(snap: dict) -> str:
    project = snap.get("project") or {}
    goal = str(project.get("goal") or "").strip()
    body = f'<p class="lead"><b>{_esc(project.get("name"))}</b></p>'
    return body + (f"<p>Цель: {_esc(goal)}</p>" if goal
                   else "<p>Цель проекта не записана в задании команды.</p>")


def _sec_result(snap: dict) -> str:
    integration, progress = snap.get("integration") or {}, snap.get("progress") or {}
    steps = "".join(
        f'<li class="{"on" if step.get("done") else ""}"><b>{_esc(step.get("title"))}</b>'
        f"<span>{_esc(step.get('text'))}</span></li>"
        for step in _list(integration.get("steps")))
    conflict = integration.get("conflict")
    trouble = (f'<p class="bad-text"><b>Что помешало:</b> {_esc(conflict.get("title"))}. '
               f"{_esc(conflict.get('detail'))}</p>" if conflict else "")
    if progress.get("total"):
        count = (f"<p>Этапов в плане: {_n(progress['total'])}. Завершено: {_n(progress['done'])}, "
                 f"выполняется: {_n(progress['active'])}, "
                 f"требует внимания: {_n(progress['needs_attention'])}.</p>")
    else:
        count = "<p>В плане пока нет задач.</p>"
    produced = []
    for task in _list(snap.get("tasks")):
        result = task.get("result") or {}
        if result.get("ready"):
            files = _codes(result.get("files"), "")
            produced.append(f"<li><b>{_esc(task.get('title'))}</b>: "
                            + (files if files else "файлов в результате нет") + "</li>")
    made = ('<ul class="plain">' + "".join(produced) + "</ul>" if produced
            else '<p class="muted">Принятых результатов пока нет.</p>')
    return (f'<p class="story">{_esc(integration.get("story"))}</p>'
            + (f'<ol class="steps">{steps}</ol>' if steps else "")
            + trouble + count + "<h3>Что подготовила команда</h3>" + made)


def _sec_done(snap: dict) -> str:
    progress = snap.get("progress") or {}
    if not progress.get("total"):
        return "<p>В плане пока нет задач.</p>"
    accepted = [t for t in _list(snap.get("tasks"))
                if (t.get("checkpoints") or {}).get("accepted")]
    if not accepted:
        return "<p>Принятых этапов пока нет.</p>"
    stages = []
    for task in accepted:
        rows = [("Исполнитель", _esc(task.get("role")))]
        if task.get("description"):
            rows.append(("Что нужно сделать", _esc(task["description"])))
        rows += [("Ожидаемый результат", _codes(task.get("expected"), "файлы не указаны")),
                 ("Полученный результат", _result(task)),
                 ("Проверка результата", _checks(task))]
        if task.get("worked_s") is not None:
            rows.append(("Длительность", _esc(_dur(task["worked_s"]))))
        stages.append(f"<li>{_title_line(task)}{_dl(rows)}</li>")
    intro = (f"Завершено {_n(progress['done'])} из {_n(progress['total'])} этапов.")
    return f"<p>{intro}</p>" + '<ol class="stages">' + "".join(stages) + "</ol>"


def _sec_not_done(snap: dict) -> str:
    tasks = _list(snap.get("tasks"))
    if not tasks:
        return "<p>В плане пока нет задач.</p>"
    # What waits for the owner, by stage: the attention card of the board.
    attention = {item.get("task"): item for item in _list(snap.get("attention"))}
    # A cancelled stage is one of them: it was in the plan and was not done.
    open_ = [t for t in tasks if not (t.get("checkpoints") or {}).get("accepted")]
    if not open_:
        return "<p>Все этапы приняты.</p>"
    stages = [f"<li>{_title_line(task)}{_dl(_open_stage(task, attention))}</li>"
              for task in open_]
    return (f"<p>Этапов не принято: {len(open_)}.</p>"
            + '<ol class="stages">' + "".join(stages) + "</ol>")


def _incidents(snap: dict) -> list[dict]:
    """The problems of the team as the recovery centre reads them, newest
    first (the order of the report); one without a time stands last."""
    return sorted(_list((snap.get("recovery") or {}).get("incidents")),
                  key=lambda item: str(item.get("when") or ""), reverse=True)


def _problem_head(number: int, incident: dict) -> str:
    """One problem named: what, on which stage, when, and whether it waits."""
    stage = incident.get("task")
    where = f"этап «{_esc(stage)}»" if stage else _esc(incident.get("affected"))
    head = f"<b>{number}. {_esc(incident.get('title'))}</b>, {where}"
    when = _fmt(incident.get("when"), "%d.%m.%Y %H:%M")
    if when:
        head += ", " + _esc(when)
    if incident.get("human_needed"):
        head += ", <b>ждёт вашего решения</b>"
    return head


def _sec_problems(snap: dict) -> str:
    recovery = snap.get("recovery") or {}
    incidents = _incidents(snap)
    if not incidents:
        return f'<p class="story">{_esc(recovery.get("story"))}</p>'
    kinds = ", ".join(f"<b>{_esc(kind.get('title'))}</b> ({_n(kind.get('count'))})"
                      for kind in _list(recovery.get("kinds")))
    repeated = ", ".join(f"{_esc(task)} ({count} "
                         + _one_of(count, "раз", "раза", "раз") + ")"
                         for task, count in (recovery.get("repeated") or {}).items())
    lines = []
    for number, incident in enumerate(incidents, 1):
        css = "open" if incident.get("human_needed") else ""
        lines.append(f'<li class="{css}"><p class="tt">{_problem_head(number, incident)}</p>'
                     + (f"<p>{_esc(incident.get('detail'))}</p>"
                        if incident.get("detail") else "") + "</li>")
    return (f'<p class="story">{_esc(recovery.get("story"))}</p>'
            + (f'<p class="small">По типам: {kinds}.</p>' if kinds else "")
            + (f'<p class="small muted">Повторялись: {repeated}.</p>' if repeated else "")
            + '<ol class="incidents">' + "".join(lines) + "</ol>")


def _sec_how_solved(snap: dict) -> str:
    incidents = _incidents(snap)
    if not incidents:
        return "<p>Проблем не было, решать было нечего.</p>"
    lines = []
    for number, incident in enumerate(incidents, 1):
        waiting = bool(incident.get("human_needed"))
        action = _esc(incident.get("action"))
        outcome = _esc(incident.get("outcome")) + (
            ". Есть риск потери результата." if incident.get("risk_of_loss") else ".")
        rows = [("Что система сделала",
                 "Система " + _esc(", ".join(_list(incident.get("did")))) + ".")]
        if incident.get("saved"):
            rows.append(("Сохранено", _esc(incident["saved"]) + "."))
        rows.append(("Итог", outcome))
        if waiting:
            rows.append(("Что нужно от вас", f"<b>{action}</b>"))
        elif incident.get("action"):
            rows.append(("Что нужно от вас", action))
        css = "open" if waiting else ""
        lines.append(f'<li class="{css}"><p class="tt">{_problem_head(number, incident)}</p>'
                     f"{_dl(rows)}</li>")
    return '<ol class="incidents">' + "".join(lines) + "</ol>"


def _sec_quality(snap: dict) -> str:
    quality = snap.get("quality") or {}
    machine, split = quality.get("machine") or {}, quality.get("split") or {}
    first = int(quality.get("first_time") or 0)
    rows = [
        ("передано на проверку", _n(quality["handed_over"]),
         "результатов, которые исполнители сдали"),
        ("прошёл с первого раза" if first == 1 else "прошли с первого раза",
         _n(first), "без доработки"),
        (_one_of(quality["reworked"], "доработка", "доработки", "доработок"),
         _n(quality["reworked"]), "результатов пришлось переделывать"),
        ("принято решением", _n(quality["accepted_by_decision"]),
         "руководитель команды принял без проверки"),
        ("Ещё на проверке", _n(split.get("awaiting") or 0), "ответ проверяющего не пришёл"),
    ]
    out = [f'<p class="story">{_esc(quality.get("story"))}</p>', _stats(rows)]
    if machine.get("run"):
        out.append("<h3>Что проверила машина</h3>" + _stats([
            (_one_of(machine["run"], "проверка", "проверки", "проверок"),
             _n(machine["run"]), "команды и файлы, которые проверяет машина"),
            ("пройдено", _n(machine["passed"]), ""),
            ("провалено", _n(machine["failed"]), "")]))
    else:
        out.append("<h3>Что проверила машина</h3>"
                   '<p class="muted">Машинных проверок не было: критерии этих задач '
                   "проверяет человек или проверяющий.</p>")
    if quality.get("checks"):
        out.append("<h3>Что оценил проверяющий</h3>" + _stats([
            (_one_of(quality["checks"], "проверка", "проверки", "проверок"),
             _n(quality["checks"]), "оценка содержания проверяющим"),
            ("пройдено", _n(quality["passed"]), ""),
            ("не пройдено", _n(quality["failed"]), "")]))
    else:
        out.append("<h3>Что оценил проверяющий</h3>"
                   '<p class="muted">Проверок ещё не было.</p>')
    # Every check in the order the answers came back, one line each.
    marks = []
    for mark in _list(quality.get("timeline")):
        passed = mark.get("verdict") == "pass"
        attempt = (f", попытка {_n(mark['attempt'])}"
                   if isinstance(mark.get("attempt"), int) else "")
        marks.append(f'<li class="{"done" if passed else "problem"}">'
                     f"{_esc(_when(mark.get('ts')))} · {_esc(mark.get('task'))}{attempt}: "
                     f"{'пройдена' if passed else 'не пройдена'}</li>")
    if marks:
        out.append('<h3>Проверки по порядку</h3><ol class="marks">' + "".join(marks)
                   + "</ol>")
    problems = _list(quality.get("problems"))
    if problems:
        out.append('<h3>Найденные недостатки</h3><ul class="plain">' + "".join(
            f'<li><b>{_esc(problem.get("task"))}</b>: '
            f'{_esc(problem.get("remark") or "замечаний не записано")} '
            f'<span class="muted">({_esc(_when(problem.get("when")))})</span></li>'
            for problem in problems) + "</ul>")
    if _list(quality.get("empty")):
        out.append('<p class="bad-text">Принято без единого файла: '
                   + _esc(", ".join(quality["empty"]))
                   + ". Исполнитель ничего не подготовил.</p>")
    if _list(quality.get("awaited")):
        out.append("<h3>Ещё ждут проверки</h3><ul class=\"plain\">" + "".join(
            f"<li>{_esc(title)}</li>" for title in quality["awaited"]) + "</ul>")
    return "".join(out)


def _sec_cost(snap: dict) -> str:
    budget = snap.get("budget") or {}
    # The story of the budget is the engine's, with one exception. When no
    # record has a price, its sentence about the spend names the known part,
    # a zero, while the spend itself is "не определена". A cost that is not
    # known is never a zero on this page: then the section opens with the
    # words of the board for the spend and for the state of the budget.
    if budget.get("spent_text") == "не определена":
        opening = f"Потрачено: не определена. {_esc(BUDGET_STATE_TEXT.get(budget.get('state'), ''))}"
    else:
        opening = _esc(budget.get("story"))
    # Every sum of records says how many of them have no price.
    unknown = int(budget.get("unknown_records") or 0)
    unpriced = f" {_reliability(unknown)}" if unknown else ""
    rows = [
        ("потрачено", _esc(budget.get("spent_text")), _reliability(unknown)),
        ("бюджет", "не согласован" if budget.get("budget_usd") is None
         else _esc(_money(budget["budget_usd"])), ""),
        ("осталось", "нет предела" if budget.get("remaining_usd") is None
         else _esc(_money(budget["remaining_usd"])), ""),
        ("в среднем на задачу", _esc(budget.get("per_task_text")),
         "по задачам, которые уже начались" + unpriced),
        ("цена принятого результата", _esc(budget.get("per_result_text")),
         "расходы на все попытки, включая доработки" + unpriced),
        ("лимит одной задачи", "не задан" if budget.get("per_task_usd") is None
         else _esc(_money(budget["per_task_usd"])),
         "после этой суммы новая попытка задачи не начнётся"),
    ]
    out = [f'<p class="story">{opening}</p>' if opening else "", _stats(rows)]
    share = budget.get("share")
    if budget.get("budget_usd") is not None and share is not None:
        # Both shares are numbers of the snapshot, shown as percentages.
        out.append(f"<p>Израсходовано {round(100 * share)}% бюджета, предупреждение "
                   f"с {round(100 * (budget.get('warn_ratio') or 0))}% бюджета.</p>")
    over = _list(budget.get("over_limit"))
    if over and budget.get("per_task_usd") is not None:
        out.append('<p class="bad-text">Лимит одной задачи ('
                   + _esc(_money(budget["per_task_usd"])) + ") исчерпан: "
                   + _esc(", ".join(over)) + ".</p>")
    out.append('<div class="duo"><div><h3>По этапам</h3>'
               + _money_list(budget.get("by_task"), budget.get("unknown_by_task"))
               + "</div><div><h3>По специалистам</h3>"
               + _money_list(budget.get("by_role"), budget.get("unknown_by_role"))
               + "</div></div>")
    return "".join(out)


def _spans(spans) -> str:
    """The time of one stage by what it was doing: every span from the
    moment it began and for how long it ran."""
    items = _list(spans)
    if not items:
        return '<span class="muted">записей о времени нет</span>'
    lines = []
    for span in items:
        length = _seconds(span.get("from"), span.get("to"))
        tone = _SPAN_TONE.get(span.get("kind"), "wait")
        how_long = f", {_esc(_dur(length))}" if length is not None else ""
        lines.append(f'<li><i class="sw sw-{tone}"></i>{_esc(span.get("text"))}: '
                     f"с {_esc(_when(span.get('from')))}{how_long}</li>")
    return '<ul class="spans">' + "".join(lines) + "</ul>"


def _sec_time(snap: dict) -> str:
    run, timeline = snap.get("run") or {}, snap.get("timeline") or {}
    finished = bool(snap.get("finished"))
    rows = [("Работа началась", _esc(_when(snap.get("created_at"))))]
    if finished and run.get("finished_at"):
        rows.append(("Работа завершена", _esc(_when(run["finished_at"]))))
    else:
        state = run.get("state") or "нет данных"
        rows.append(("Работа ещё идёт", _esc(f"текущее состояние: {state}")))
    took = ("работа заняла " if finished else "в работе ") + _dur(snap.get("elapsed_s") or 0)
    rows.append(("Продолжительность", _esc(took)))
    worked = {t.get("id"): t.get("worked_s") for t in _list(snap.get("tasks"))}
    table = []
    for line in _list(timeline.get("rows")):
        spent = worked.get(line.get("id"))
        table.append((
            f"<b>{_esc(line.get('title'))}</b><br>{_badge(line.get('state'), line.get('kind'))}",
            _spans(line.get("spans")),
            _esc(_dur(spent)) if spent is not None else "-"))
    out = [_dl(rows)]
    if table:
        out.append(_table(("Этап", "Чем был занят этап", "Время в работе"), table, "time"))
        if not finished:
            out.append('<p class="muted small">Отрезок времени, который ещё не '
                       "закончился, считается до момента составления отчёта.</p>")
    else:
        out.append("<p>В плане пока нет задач.</p>")
    return "".join(out)


def _sec_team(snap: dict) -> str:
    agents = _list(snap.get("agents"))
    if not agents:
        return '<p class="muted">Специалистов пока нет: план ещё не сформирован.</p>'
    cards = []
    for agent in agents:
        name, state = agent.get("name"), agent.get("state")
        rows = [("Текущая работа", _esc(agent.get("doing")))]
        if agent.get("checks") is not None:
            # The checker is counted by its checks, never by stages.
            checks = f"Проверок выполнено: {_n(agent['checks'])}"
            if agent.get("completed"):
                checks += f", принято по проверке: {_n(agent['completed'])}"
            rows.append(("Вклад", _esc(checks)))
        else:
            # The stages of the specialist, each with its state (and with
            # the role it wears there, when a specialist wears several).
            stages = "".join(
                f"<li>{_esc(t.get('title'))}: {_esc(t.get('state'))}"
                + (f" (как {_esc(t.get('role'))})" if t.get("role") != name else "")
                + "</li>"
                for t in _list(agent.get("tasks")))
            share = _esc(f"завершено {_n(agent['completed'])} из {_n(agent['total'])}")
            rows.append(("Вклад", share + (f'<ul class="inline">{stages}</ul>' if stages
                                           else "")))
        if agent.get("worked_s") is not None:
            rows.append(("Время в работе", _esc(_dur(agent["worked_s"]))))
        note = ""
        if agent.get("checks") is not None:
            note = ('<p class="muted small">Результат, принятый руководителем своим '
                    "решением, в счёт проверок не входит.</p>")
        hint = _esc(agent.get("state_hint"))
        cards.append(f'<li><p class="tt"><b>{_esc(name)}</b> '
                     f"{_badge(state, _AGENT_TONE_BY_LABEL.get(state))}"
                     + (f' <span class="muted small">{hint}</span>' if hint else "")
                     + f'</p><p class="does">{_esc(agent.get("does"))}</p>'
                     f"{_dl(rows)}{note}</li>")
    return '<ul class="team">' + "".join(cards) + "</ul>"


def _sec_history(snap: dict) -> str:
    tasks = _list(snap.get("tasks"))
    if not tasks:
        return "<p>В плане пока нет задач.</p>"
    history = snap.get("task_events") or {}
    blocks = []
    for task in tasks:
        events = sorted(_list(history.get(task.get("id"))),
                        key=lambda item: item.get("seq") or 0)   # oldest first
        lines = []
        for event in events:
            detail = str(event.get("detail") or "").rstrip(".")
            who = (_esc(event["specialist"]) if event.get("specialist") else "")
            lines.append(f'<li><time>{_esc(_when(event.get("ts")))}</time>'
                         + (f'<span class="who">{who}</span>' if who else "")
                         + f"<span>{_esc(event.get('title'))}"
                         + (f". {_esc(detail)}" if detail else "") + "</span></li>")
        body = ('<ol class="history">' + "".join(lines) + "</ol>" if lines
                else '<p class="muted">Событий пока не было.</p>')
        blocks.append(f'<h3>{_esc(task.get("title"))} '
                      f"{_badge(task.get('state'), task.get('kind'))}</h3>{body}")
    return "".join(blocks)


def _plain(value) -> str:
    """A value of the config as text: a word as it is, the rest as JSON."""
    if value is None:
        return "не задано"
    if isinstance(value, str):
        return value or "-"
    return json.dumps(value, ensure_ascii=False)


def _sec_tech(snap: dict) -> str:
    """What only a technical reader needs. It stands inside the one
    collapsible section of the page, after everything said in words."""
    tech = snap.get("tech") or {}
    run_tech = (snap.get("run") or {}).get("tech") or {}
    build = (snap.get("integration") or {}).get("tech") or {}
    out = ["<h3>Команда и запуск</h3>", _dl([
        ("Идентификатор команды", _code(snap.get("team"))),
        ("Каталог проекта", _code((snap.get("project") or {}).get("root"))),
        ("Идентификатор запуска", _code(run_tech.get("run_id"))),
        ("Статус запуска", _code(run_tech.get("status"))),
        ("Уровень данных", _code(snap.get("level"))),
    ])]
    if any(build.get(key) for key in ("branch", "base_commit", "commit_sha", "merged",
                                      "conflict")):
        merged = "; ".join(
            f"{_esc(m.get('task'))}: {_esc(m.get('sha'))}" if isinstance(m, dict)
            else _esc(m) for m in _list(build.get("merged")))
        clash = _esc(_plain(build["conflict"])) if build.get("conflict") else ""
        out.append("<h3>Объединение результатов</h3>" + _dl([
            ("Ветка объединения", _code(build.get("branch"))),
            ("База объединения", _code(build.get("base_commit"))),
            ("Результат объединения", _code(build.get("commit_sha"))),
            ("Объединённые коммиты", merged),
            # The clash exactly as the engine wrote it (v2.1 spec 13).
            ("Противоречие при объединении", clash),
        ]))
    stages = []
    for task in _list(snap.get("tasks")):
        own = task.get("tech") or {}
        stages.append((
            _code(task.get("id")), _esc(task.get("title")), _code(own.get("status")),
            _esc(task.get("state")), _code(own.get("assignee")), _code(own.get("branch")),
            _code((task.get("result") or {}).get("version"))))
    out.append("<h3>Этапы</h3>" + _table(
        ("Идентификатор", "Название", "Статус", "Состояние", "Исполнитель", "Ветка",
         "Версия результата"), stages, "wide") if stages else "<h3>Этапы</h3><p>В плане пока нет задач.</p>")
    out.append("<h3>Конфигурация</h3>" + _table(
        ("Параметр", "Значение"),
        [(_code(key), _esc(_plain(value)))
         for key, value in (tech.get("config") or {}).items()], "config"))
    models = _list((snap.get("cost") or {}).get("models"))
    if models:
        rows = []
        for model in models:
            price = model.get("price_per_1m") or {}
            spent = _esc(_money(model["usd"])) if model.get("usd") else "не определена"
            unknown = int(model.get("unknown") or 0)
            if unknown:
                spent += (f' <span class="muted">({unknown} '
                          + _one_of(unknown, "запись", "записи", "записей") + " без цены)</span>")
            rows.append((
                _esc(model.get("model")), _esc(model.get("provider")), _n(model["records"]),
                f"{_grouped(model['input_tokens'])} / {_grouped(model['output_tokens'])}",
                f"{_grouped(model['cache_read_tokens'])} / {_grouped(model['cache_write_tokens'])}",
                (f"{_esc(_money(price['input']))} / {_esc(_money(price['output']))} / "
                 f"{_esc(_money(price['cache_read']))}" if price
                 else "модели нет в таблице цен"),
                spent,
                _esc(", ".join(_QUALITY_TEXT.get(q, q) for q in _list(model.get("quality"))))
                or "-"))
        out.append("<h3>По моделям</h3>" + _table(
            ("Модель", "Поставщик", "Записей", "Токены: вход / выход", "Кеш: чтение / запись",
             "Цена за 1 млн: вход / выход / кеш", "Расход", "Качество цены"), rows, "wide"))
    events = sorted(_list(snap.get("events")), key=lambda item: item.get("seq") or 0)
    if events:
        journal = [(_n(e.get("seq")), _esc(_fmt(e.get("ts"), "%d.%m.%Y %H:%M:%S")),
                    _code(e.get("type")), _esc(e.get("task")) or "-",
                    _code((e.get("tech") or {}).get("actor_id")), _esc(e.get("title")))
                   for e in events]
        out.append('<h3>Журнал событий</h3>'
                   f'<p class="muted small">Записей: {_n(len(events))}.</p>'
                   + _table(("№", "Время", "Тип события", "Этап", "Актор (идентификатор)",
                             "Запись"), journal, "journal"))
    else:
        out.append("<h3>Журнал событий</h3><p class=\"muted\">Событий пока нет.</p>")
    return "".join(out)


# ----------------------------------------------------------------- the page

_CSS = """
  body { margin: 0; padding: 2rem 1.25rem 4rem; background: #f6f6f4; color: #1a1a1a;
         font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
         font-size: 16px; line-height: 1.55; }
  main { max-width: 62rem; margin: 0 auto; }
  h1 { font-size: 1.65rem; margin: .15rem 0 .6rem; line-height: 1.25; overflow-wrap: anywhere; }
  h2 { font-size: 1.2rem; margin: 0 0 .9rem; }
  h3 { font-size: 1rem; margin: 1.4rem 0 .45rem; }
  header h3, section > h3:first-of-type { margin-top: 1rem; }
  p { margin: .3rem 0 .75rem; }
  a { color: #1d4ed8; }
  code { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: .88em;
         background: #f1f1ef; padding: .05rem .3rem; border-radius: 4px; overflow-wrap: anywhere; }
  .head, section, details { background: #fff; border: 1px solid #e3e3e0; border-radius: 10px;
                            padding: 1.15rem 1.4rem; margin: 1rem 0; }
  .kicker { margin: 0; color: #666; font-size: .82rem; letter-spacing: .08em;
            text-transform: uppercase; }
  .meta { color: #444; font-size: .92rem; margin: .12rem 0; }
  .muted, small, .note { color: #666; }
  .small { font-size: .88rem; }
  .story { font-size: 1.06rem; }
  .lead { font-size: 1.12rem; margin: 0 0 .3rem; }
  .bad-text { color: #8a1420; }
  .foot { color: #777; font-size: .85rem; margin: 1.5rem 0 0; }
  .state { display: inline-block; padding: .02rem .6rem; border-radius: 999px; font-size: .82rem;
           font-weight: 600; }
  .idle { background: #e9ecef; color: #3f4852; }
  .busy { background: #fff3cd; color: #6b4e00; }
  .attention { background: #ffe0b2; color: #7a3e00; }
  .done { background: #d8f3dc; color: #1d5c2a; }
  .problem { background: #f8d7da; color: #8a1420; }
  .manual { background: #ffe9c7; color: #7a4a00; }
  .toc { margin: 1rem 0 0; padding: .7rem 1rem .8rem; background: #f8f8f6; border-radius: 8px; }
  .toc p { margin: 0 0 .3rem; font-weight: 600; font-size: .92rem; }
  .toc ol { margin: 0; padding-left: 1.3rem; columns: 2; column-gap: 2rem; font-size: .92rem; }
  .toc li { break-inside: avoid; }
  .facts { display: grid; grid-template-columns: minmax(9rem, 13rem) 1fr; gap: .15rem 1rem;
           margin: .5rem 0 .1rem; }
  .facts dt { color: #555; }
  .facts dd { margin: 0; overflow-wrap: anywhere; }
  .tt { margin: 0 0 .1rem; }
  .does { margin: 0 0 .1rem; color: #333; }
  ul.plain, ol.stages, ul.team { list-style: none; padding: 0; margin: .4rem 0; }
  .stages > li, .team > li { border-top: 1px solid #ececea; padding: .75rem 0 .25rem; }
  .stages > li:first-child, .team > li:first-child { border-top: 0; padding-top: .1rem; }
  ol.checks, ol.marks { margin: 0; padding-left: 1.5rem; }
  ol.checks li, ol.marks li { margin: .1rem 0; }
  ol.steps { list-style: none; padding: 0; margin: .7rem 0; display: grid; gap: .4rem; }
  ol.steps li { display: flex; justify-content: space-between; gap: 1rem; padding: .45rem .8rem;
                border-radius: 8px; background: #f3f3f1; }
  ol.steps li.on { background: #d8f3dc; }
  ol.incidents { padding-left: 1.4rem; margin: .6rem 0; }
  ol.incidents > li { margin: .2rem 0 1rem; }
  ol.incidents > li.open { border-left: 3px solid #c9184a; padding-left: .75rem; }
  table { border-collapse: collapse; width: 100%; font-size: .92rem; margin: .5rem 0; }
  th, td { text-align: left; vertical-align: top; padding: .35rem .7rem .35rem 0;
           border-bottom: 1px solid #ececea; }
  thead th { color: #555; font-weight: 600; }
  table.stats { max-width: 40rem; }
  table.stats th { font-weight: 500; }
  table.stats small { display: block; font-size: .8rem; }
  table.wide, table.journal { font-size: .85rem; }
  table.journal td:first-child { white-space: nowrap; color: #555; }
  ul.sums { list-style: none; padding: 0; margin: .3rem 0; }
  ul.sums li { display: flex; flex-wrap: wrap; gap: .15rem .6rem; padding: .2rem 0;
               border-bottom: 1px solid #f1f1ef; }
  ul.sums .name { flex: 1 1 10rem; }
  ul.sums .value { font-weight: 600; white-space: nowrap; }
  ul.sums .note { font-size: .85rem; flex-basis: 100%; }
  ul.inline { margin: .1rem 0 .2rem; padding-left: 1.1rem; }
  .duo { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 0 2rem; }
  ul.spans { list-style: none; margin: 0; padding: 0; }
  ul.spans li { display: flex; align-items: baseline; gap: .45rem; }
  .sw { flex: 0 0 .55rem; width: .55rem; height: .75rem; border-radius: 3px; display: inline-block;
        background: #ced4da; align-self: center; }
  .sw-work { background: #8ecae6; }
  .sw-check { background: #a9c4e8; }
  .sw-fix { background: #f4a261; }
  .sw-wait { background: #ced4da; }
  .sw-recovery { background: #c77dff; }
  .sw-done { background: #95d5a4; }
  .sw-stopped { background: #e07a5f; }
  ol.history { list-style: none; padding: 0; margin: .3rem 0; }
  ol.history li { display: flex; flex-wrap: wrap; gap: .1rem .7rem; padding: .2rem 0;
                  border-bottom: 1px solid #f1f1ef; font-size: .93rem; }
  ol.history time { flex: 0 0 10.6rem; color: #666; }
  ol.history .who { flex: 0 0 13rem; }
  summary { font-size: 1.2rem; font-weight: 600; cursor: pointer; }
  details[open] summary { margin-bottom: .9rem; }
  @media (max-width: 640px) {
    .facts { grid-template-columns: 1fr; gap: 0; }
    .facts dd { margin-bottom: .5rem; }
    .toc ol { columns: 1; }
    ol.steps li { flex-direction: column; gap: .1rem; }
    ol.history time, ol.history .who { flex-basis: 100%; }
  }
  @media print {
    body { background: #fff; padding: 0; font-size: 12px; }
    .head, section, details { border: 0; padding: 0; margin: 1.2rem 0; }
    .toc { display: none; }
  }
"""


def _header(snap: dict) -> str:
    project, run = snap.get("project") or {}, snap.get("run") or {}
    state = run.get("state") or "нет данных"
    hint = run.get("hint")
    zone = _zone(snap.get("now"))
    contents = "".join(
        f'<li><a href="#{section_id}">{number}. {_esc(heading)}</a></li>'
        for number, (section_id, heading) in enumerate(SECTIONS, 1))
    return (f'<header class="head"><p class="kicker">Отчёт о работе команды</p>'
            f'<h1>{_esc(project.get("name"))}</h1>'
            f'<p class="meta">Состояние работы: <b>{_esc(state)}</b>'
            + (f" ({_esc(hint)})" if hint else "")
            + f'.</p><p class="meta">Отчёт составлен: {_esc(_when(snap.get("now")))}.</p>'
            + (f'<p class="meta">Время в отчёте указано в часовом поясе {_esc(zone)}.</p>'
               if zone else "")
            + f'<nav class="toc" aria-label="Содержание"><p>Содержание</p><ol>{contents}</ol></nav>'
            "</header>")


def _page(snap: dict) -> str:
    """The whole document from one snapshot."""
    told = (_sec_goal, _sec_result, _sec_done, _sec_not_done, _sec_problems,
            _sec_how_solved, _sec_quality, _sec_cost, _sec_time, _sec_team, _sec_history)
    if len(told) + 1 != len(SECTIONS):
        raise RuntimeError("the report must have one builder for every section but the last")
    parts = []
    for number, ((section_id, heading), build) in enumerate(zip(SECTIONS, told), 1):
        parts.append(f'<section id="{section_id}"><h2>{number}. {_esc(heading)}</h2>'
                     f"{build(snap)}</section>")
    last, (section_id, heading) = len(SECTIONS), SECTIONS[-1]
    parts.append(f'<details id="{section_id}"><summary>{last}. {_esc(heading)}</summary>'
                 f"{_sec_tech(snap)}</details>")
    name = _esc((snap.get("project") or {}).get("name"))
    return (
        '<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Отчёт о работе команды: {name}</title>\n<style>\n{_CSS}</style>\n"
        f"</head>\n<body>\n<main>\n{_header(snap)}\n" + "\n".join(parts)
        + "\n<footer class=\"foot\">Отчёт составлен по записям, которые система вела во время "
        "работы команды. Ни одно число здесь не оценено и не добавлено от себя.</footer>\n"
        "</main>\n</body>\n</html>\n")


def render_report(store: Store, team: str) -> str:
    """The report of one team as one HTML document.

    The project root is read from the row of the team, and everything
    else from the read-only snapshot the board is built from. Nothing in
    the team's state is touched: the snapshot opens the database read-only.
    """
    row = store.get_team(team)
    if row is None:
        raise NotFoundError(f"team not found in state database: {team}")
    snap = dashboard_data.snapshot(row["project_root"], team, level="advanced",
                                   event_limit=_WHOLE_JOURNAL, parts=("task_events",),
                                   whole=True)
    return _page(snap)


def write_report(store: Store, team: str, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(store, team), encoding="utf-8")
    return out_path
