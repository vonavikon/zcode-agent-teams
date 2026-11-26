"""A brief in plain words becomes questions, a plan and a specification.

A person who is not technical must not write configurations by hand or
learn the command line (v2.1 specification, section 14). In this product the lead
talks with the owner and fills a **brief**: a mapping of human words,
with no identifier and no knowledge of the engine in it. This module
turns the brief into three things:

- the questions to ask when something critical is missing: one clear
  question at a time, the most important first;
- the plan the owner confirms before the start: the stages, the
  specialists, the budget and the constraints, in plain words;
- the task specification that ``team.py init`` hands to the engine,
  built only when nothing is missing and nothing is written wrongly.

``review(brief)`` is the one entry point. It answers and never raises
for a brief that is a mapping; only a brief that is not a mapping at
all raises ``BriefError``. An engine message is never shown to the owner
as a question: whatever the engine's own check reports becomes a
problem in words, with the original text kept beside it in ``detail``.

Nothing here looks at the disk. The files of a stage are checked with
the engine's own path rule, but a file that leaves the project through
a link, or two names that differ only by letter case on a disk that
ignores case, are seen by the engine when the team is created.

The brief, field by field
-------------------------

Every field is optional unless it says "required". Unknown fields are
ignored: the lead may keep notes there. The words are the owner's, in
any language; only the field names are fixed.

``title``
    The name of the team for people, up to 120 signs. Without it the
    first 120 signs of the request become the name, and without a
    request the name is "AI-команда".

``request`` (required)
    What the owner asks the team to do, one text of up to 1000 signs.
    Without it the owner is asked what to ask the team.

``result``
    The wanted result, in words. It is written after the request in the
    goal of the team, preceded by "Ожидаемый результат: ". The request
    and the result together must fit in 1000 signs.

``constraints``
    A list of short texts of what must hold ("только открытые
    источники"). The plan repeats them, and its summary says when there
    are none.

``materials``
    A list of what the team may read: files of the project, links,
    names of documents. The plan shows the list as it is.

``deadline``
    The expected date, as free text ("к 1 декабря"). The plan shows it
    as it is.

``budget_usd``
    The agreed budget: one number above zero, in dollars. Leave it out
    while the budget is not agreed: nothing asks for it, and the plan
    says "бюджет не согласован".

``quality``
    A list of short texts of what "good" means here ("каждый вывод со
    ссылкой на источник"). The plan shows the list as it is.

``stages`` (required, at least one)
    The plan in the order of the work. A team cannot start without at
    least one stage. Each stage is a mapping with these fields:

    ``title`` (required)
        The name of the stage, unique in the brief. Other stages name
        it in ``after``, and the owner sees it on the board.
    ``description``
        What the stage is about, one text of up to 500 signs.
    ``specialist``
        Who does it, by key: ``analyst`` (AI-аналитик), ``writer``
        (AI-редактор документации), ``engineer`` (AI-разработчик),
        ``security`` (AI-специалист по безопасности), ``performance``
        (AI-аналитик производительности). Without it the stage is done
        by ``specialist`` (AI-специалист), the default.
    ``writes`` (required)
        The files the stage creates or changes, a list of paths inside
        the project folder ("reports/competitors.md"). The engine's own
        path rule applies: no absolute path, no "..", nothing in
        ".agent-teams" or ".git", no Windows drive letter. Without it
        the owner is asked what must come out of the stage.
    ``done_when`` (required)
        How to see that the stage is done, a list of short texts ("в
        таблице не меньше пяти конкурентов"). Each becomes a criterion
        that is confirmed by a person. Without it the owner is asked
        how to see that the stage is done.
    ``after``
        The titles of the stages this one waits for. They become the
        dependencies of the task. Every title must be in the plan, and
        the waiting must not go in a circle.
    ``check``
        A command that must pass at the end of the stage ("python -m
        doctest reports/competitors.md"). It becomes a criterion the
        machine checks.

``config``
    Engine settings for advanced users (timeouts, the workspace mode,
    money limits): a mapping merged as it is into the configuration of
    the team. The budget above goes in as ``budget_usd``; a value here
    with the same name has the last word. Anything the engine does not
    accept is reported as a problem, with the engine's text beside it.

What is missing and what is wrong
---------------------------------

Only four things are critical, and only they are asked for: the
request, at least one stage, the files of every stage and the way to
see that every stage is done. Everything else may be absent: the
system can start safely without it, so it is never asked for and the
plan shows it as not given. What is written wrongly is a problem, in
words a person who never saw the engine understands, and each problem
names the stage it is about.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import validation, viewmodel
from .errors import UsageError
from .validation import (TEXT_LIMITS, find_cycle, find_write_overlaps,
                         normalize_path, validate_team_spec)

__all__ = ["BriefError", "SPECIALISTS", "review"]


class BriefError(ValueError):
    """The brief is not a mapping at all: there is nothing to review."""


# The specialists, by key, with the lens the task gets. The name the
# board gives each is looked up in the viewmodel, so the plan and the
# board can never disagree about it.
SPECIALISTS = {
    "analyst": "behavior",
    "writer": "docs",
    "engineer": "correctness",
    "security": "security",
    "performance": "performance",
    "specialist": None,
}
DEFAULT_SPECIALIST = "specialist"
DEFAULT_TITLE = "AI-команда"
_RESULT_LINE = "Ожидаемый результат: "
_CHECK_PASSED = "команда проходит: "

# The four questions, the most important first.
_ASK_REQUEST = "Что вы просите команду сделать?"
_ASK_STAGES = "Из каких этапов состоит работа? Назовите хотя бы один этап."
_ASK_WRITES = ("Какие файлы должны получиться на {stage}? Назовите хотя бы один "
               "файл, который этот этап создаст или изменит.")
_ASK_DONE = "Как понять, что {stage} готов и можно двигаться дальше?"

# The fault of a file, by the class the engine's own rule gives it.
_PATH_FAULTS = {
    "ABSOLUTE": "Файл «{file}» в {stage} указан как путь от начала диска. "
                "Нужен путь внутри папки проекта, например «reports/competitors.md».",
    "TRAVERSAL": "В имени файла «{file}» в {stage} есть «..». Нужен простой путь "
                 "внутри папки проекта, например «docs/notes.md».",
    "RESERVED": "Файл «{file}» в {stage} лежит в служебной папке, которую система "
                "бережёт для себя. Выберите другое место внутри проекта.",
    "": "Файл «{file}» в {stage} записан так, что система его не примет. "
        "Нужен простой путь внутри папки проекта, например «docs/notes.md».",
}

_TASK_ID = "stage-"
_AGENT_ID = "spec-"
_TASK_REF = re.compile(r"tasks\[([^\]]+)\]")
_TASK_NAME_REF = re.compile(re.escape(_TASK_ID) + r"\d+\b")


@dataclass
class _Stage:
    """One stage of the brief as far as it could be read.

    ``n`` is the place of the stage in the brief, from 1. ``notes`` are
    the problems about it. ``flagged`` says that a question was asked or
    a problem was said: it tells the engine's own check, further down,
    that the stage was already spoken about.
    """
    n: int
    title: str | None = None            # None when there is no usable title
    description: str | None = None
    lens: str | None = None             # None for the default specialist
    role: str = ""                      # the name the board gives
    writes: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    check: str | None = None
    waits_for: list[int] = field(default_factory=list)   # places of other stages
    flagged: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def stem(self) -> str:
        """How the stage is named in a sentence: by its title or by its place."""
        return f"«{self.title}»" if self.title else f"номер {self.n}"

    def say(self, text: str) -> None:
        """A problem about this stage."""
        self.flagged = True
        self.notes.append(text)


# ---------------------------------------------------------------- words


def _kind(value: object) -> str:
    """What a value of the wrong shape is, in words."""
    if isinstance(value, bool):
        return "ответом «да» или «нет»"
    if isinstance(value, str):
        return "одним текстом"
    if isinstance(value, (int, float)):
        return "числом"
    if isinstance(value, dict):
        return "одним набором полей"
    if isinstance(value, (list, tuple)):
        return "списком"
    if value is None:
        return "пустотой"
    return "чем-то другим"


def _count(n: object, one: str, few: str, many: str) -> str:
    """A count with the right ending: 1 специалист, 2 специалиста, 5 специалистов."""
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return f"{n} {many}"
    if n != int(n):
        return f"{n} {few}"             # a fraction: 1.5 доллара
    n = int(n)
    last, tenth = n % 10, n % 100
    if last == 1 and tenth != 11:
        word = one
    elif last in (2, 3, 4) and tenth not in (12, 13, 14):
        word = few
    else:
        word = many
    return f"{n} {word}"


def _and(parts: list[str]) -> str:
    """A list inside a sentence: «A»; «A» и «B»; «A», «B» и «C»."""
    if len(parts) < 2:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " и " + parts[-1]


def _is_money(value: object) -> bool:
    """A number above zero, as the engine understands a budget."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return False
    return isinstance(value, int) or math.isfinite(value)


def _role(key: str) -> str:
    """The name the board gives a specialist key."""
    return viewmodel.role_name({"lens": SPECIALISTS.get(key)})


def _is_list(value: object) -> bool:
    return isinstance(value, (list, tuple))


def _texts(value: list) -> list[str]:
    return [item for item in value if isinstance(item, str)]


def _all_texts(value: list) -> bool:
    return all(isinstance(item, str) and item.strip() for item in value)


def _read_text(value: object, say, subject: str, verb: str = "должно быть") -> str | None:
    """A field that must be one text; None when it is absent or unusable.

    A blank text is as good as an absent one. A value of another shape
    is a problem, said once.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        say(f"{subject} {verb} одним текстом, а не {_kind(value)}.")
        return None
    return value if value.strip() else None


def _read_list(value: object, say, subject: str, each: str) -> list[str]:
    """A field that must be a list of texts: the texts found, possibly few.

    ``subject`` and ``each`` end with the verb ("Ограничения должны",
    "Каждое ограничение должно"). None and an empty list are both
    "nothing" and are not a problem here: the caller decides whether
    nothing is asked for.
    """
    if value is None:
        return []
    if not _is_list(value):
        say(f"{subject} быть списком, а не {_kind(value)}.")
        return []
    if not _all_texts(value):
        say(f"{each} быть одним текстом, без пустых строк.")
    return _texts(value)


def _path_fault(file: str) -> str | None:
    """The words for a file the engine's path rule refuses; None when it is fine.

    The rule is the engine's (``validation.normalize_path``) and so is
    the class of the fault (``validation._path_code``): no second
    reading of either is written here.
    """
    try:
        normalize_path(Path("."), file, check_fs=False)
    except UsageError as exc:
        return _PATH_FAULTS.get(validation._path_code(exc), _PATH_FAULTS[""])
    return None


def _names(stages) -> str:
    """Stages as a sentence begins: «Этап «A» и этап номер 2»."""
    names = _and([f"этап {stage.stem}" for stage in stages])
    return names[0].upper() + names[1:]


def _overlap_words(stage: _Stage, others: list, files: list[str]) -> str:
    """A stage that changes a place other stages change too, with no order between them."""
    names = _and([f"этап {other.stem}" for other in others])
    if len(files) == 1:
        what = f"меняет файл «{files[0]}», который меняют ещё {names}"
    else:
        listed = ", ".join(f"«{file}»" for file in files)
        what = f"меняет место в проекте, которое меняют ещё {names} ({listed})"
    return (f"{_names([stage])} {what}, но не сказано, какой из них идёт раньше: "
            "один может затереть работу другого. Укажите порядок этапов или "
            "доверьте это место только одному из них.")


# ---------------------------------------------------------------- shapes


def _acceptance(stage: _Stage) -> list[dict]:
    """The ways to see that the stage is done, and the command that must pass."""
    # A blank line is a problem of the brief, said by ``review``; it is
    # left out here so that the engine's check speaks only of what is left.
    criteria = [{"id": f"AC-{number:03d}", "description": text, "type": "manual"}
                for number, text in enumerate(
                    (text for text in stage.done_when if text.strip()), 1)]
    if stage.check is not None:
        criteria.append({"id": f"AC-{len(criteria) + 1:03d}",
                         "description": _CHECK_PASSED + stage.check,
                         "type": "test", "verification": stage.check})
    return criteria


def _specification(title: str, goal: str, budget, config: dict,
                   stages: list[_Stage]) -> dict:
    """The specification for ``team.py init``, from what could be read.

    It is built even for a brief that is not ready: the engine's own
    check is run on it, and only a brief with nothing left to say keeps
    it (``review``).
    """
    merged = {**({"budget_usd": budget} if budget is not None else {}), **config}
    tasks = []
    for stage in stages:
        task = {"id": f"{_TASK_ID}{stage.n}", "title": stage.title or ""}
        if stage.description is not None:
            task["description"] = stage.description
        task["assignee"] = f"{_AGENT_ID}{stage.n}"
        if stage.lens is not None:
            task["lens"] = stage.lens
        task["files_write"] = list(stage.writes)
        task["acceptance"] = _acceptance(stage)
        task["deps"] = [f"{_TASK_ID}{n}" for n in stage.waits_for]
        tasks.append(task)
    return {"title": title, "goal": goal, "config": merged,
            "agents": [{"id": f"{_AGENT_ID}{stage.n}", "role": "executor"}
                       for stage in stages],
            "tasks": tasks}


def _place(task_id: str) -> int:
    return int(task_id[len(_TASK_ID):])


def _mentioned(issue, stages: list[_Stage], by_place: dict[int, _Stage]) -> list[_Stage]:
    """The stages an issue of the engine is about, by the engine's own words.

    The engine names a task in two ways: by its place in the list inside
    the loop over the tasks, and by its identifier in the loop over what
    waits for what. A circle names its members between arrows.
    """
    ids = issue.task_ids or tuple(dict.fromkeys(_TASK_REF.findall(issue.message)))
    if not ids:
        ids = tuple(dict.fromkeys(_TASK_NAME_REF.findall(issue.message)))
    found = []
    for ref in ids:
        if ref.isdigit():
            if int(ref) < len(stages):
                found.append(stages[int(ref)])
        elif ref.startswith(_TASK_ID) and _place(ref) in by_place:
            found.append(by_place[_place(ref)])
    return found


def _unsaid(errors, stages: list[_Stage], goal_said: bool, title_said: bool,
            stages_said: bool) -> list[dict]:
    """What the engine's check reports and nothing here said already.

    An issue about a stage that was already asked about or reported is
    the same thing in the engine's words: it is not repeated. So is an
    issue about the request, the name of the team or the missing stages
    when they were already spoken about. Everything else is kept, in
    words, with the engine's text beside it.
    """
    extra = []
    by_place = {stage.n: stage for stage in stages}
    for issue in errors:
        found = _mentioned(issue, stages, by_place)
        if found:
            if all(stage.flagged for stage in found):
                continue
            stage = next((one for one in found if not one.flagged), found[0])
        else:
            stage = None
            if issue.code == "E_TASKS_EMPTY" and stages_said:
                continue
            if issue.code == "E_TEXT":
                if "goal" in issue.message and goal_said:
                    continue
                if "title" in issue.message and title_said:
                    continue
        if issue.code == "E_CONFIG_INVALID":
            what = ("Дополнительные настройки написаны с ошибкой: руководитель команды "
                    "должен их поправить.")
        elif stage is not None:
            what = (f"Система не приняла этап {stage.stem}: руководитель команды должен "
                    "посмотреть подробности и поправить его.")
        else:
            what = ("Система не приняла план целиком: руководитель команды должен "
                    "посмотреть подробности и поправить его.")
        extra.append({"stage": stage.title if stage else None, "what": what,
                      "detail": issue.message})
    return extra


def _plan(title: str, goal: str, budget, constraints: list[str], materials: list[str],
          deadline: str | None, quality: list[str], stages: list[_Stage]) -> dict:
    """What will be done, for the confirmation: always, from what the brief has."""
    roles: dict[str, int] = {}
    for stage in stages:
        roles[stage.role] = roles.get(stage.role, 0) + 1
    money = (f"бюджет {_count(budget, 'доллар', 'доллара', 'долларов')}"
             if budget is not None else "бюджет не согласован")
    limits = "ограничения: " + ", ".join(constraints) if constraints else "ограничений нет"
    return {
        "title": title,
        "goal": goal,
        "stage_count": len(stages),
        "stages": [{
            "title": stage.title,
            "specialist": stage.role,
            "after": [name.strip() for name in stage.after if name.strip()],
            "result": [file for file in stage.writes if file.strip()],
            "done_when": [text for text in stage.done_when if text.strip()],
            "description": stage.description,
        } for stage in stages],
        "specialists": [{"name": name, "stages": count}
                        for name, count in roles.items()],
        "budget_usd": budget,
        "constraints": list(constraints),
        "materials": list(materials),
        "deadline": deadline,
        "quality": list(quality),
        "summary": (f"В работе {_count(len(roles), 'специалист', 'специалиста', 'специалистов')}"
                    f" и {_count(len(stages), 'этап', 'этапа', 'этапов')}, "
                    f"{money}, {limits}."),
    }


# ---------------------------------------------------------------- review


def review(brief: dict) -> dict:
    """Answer a brief: the questions, the problems, the plan and the specification.

    ``ok`` is True only when no question and no problem is left; only
    then the answer carries the specification. The module docstring
    tells the brief field by field, what is asked for and what is
    reported.
    """
    if not isinstance(brief, dict):
        raise BriefError(f"the brief must be a mapping, got {type(brief).__name__}")

    questions: list[str] = []
    ask_files: list[str] = []       # the questions about the files of a stage
    ask_done: list[str] = []        # the questions about the end of a stage
    whole: list[str] = []           # the problems about the whole brief
    # What was already said, so that the engine's own check, further
    # down, does not say the same thing again in its own words.
    goal_said = title_said = stages_said = False

    def say(text: str) -> None:
        whole.append(text)

    # ---- the request: what the owner asks the team to do
    request = brief.get("request")
    request_text = ""
    if isinstance(request, str) and request.strip():
        request_text = request
        if len(request) > TEXT_LIMITS["goal"]:
            say(f"Просьба команде длиннее {TEXT_LIMITS['goal']} знаков: сократите её, "
                "подробности можно перенести в описания этапов.")
            goal_said = True
    elif request is None or isinstance(request, str):
        questions.append(_ASK_REQUEST)
        goal_said = True
    else:
        say(f"Просьба команде должна быть одним текстом, а не {_kind(request)}.")
        goal_said = True

    result = _read_text(brief.get("result"), say, "Описание ожидаемого результата")
    goal = request_text
    if result:
        goal += "\n" + _RESULT_LINE + result
        if not goal_said and len(goal) > TEXT_LIMITS["goal"]:
            say(f"Просьба команде вместе с ожидаемым результатом длиннее "
                f"{TEXT_LIMITS['goal']} знаков: сократите один из них.")
            goal_said = True

    # ---- the name of the team
    title = brief.get("title")
    if title is not None and not isinstance(title, str):
        say(f"Название команды должно быть одним текстом, а не {_kind(title)}.")
        title = None
    elif isinstance(title, str):
        if not title.strip():
            title = None
        elif len(title) > TEXT_LIMITS["title"]:
            say(f"Название команды длиннее {TEXT_LIMITS['title']} знаков: сократите его.")
            title_said = True
    if title is None:
        # The first 120 signs of the request, as they are. Blank lines
        # before its first word are not a name, so they are skipped.
        title = request_text.lstrip()[:TEXT_LIMITS["title"]] or DEFAULT_TITLE

    # ---- what may be absent and is never asked for
    constraints = _read_list(brief.get("constraints"), say, "Ограничения должны",
                             "Каждое ограничение должно")
    materials = _read_list(brief.get("materials"), say, "Материалы должны",
                           "Каждый материал должен")
    quality = _read_list(brief.get("quality"), say, "Требования к качеству должны",
                         "Каждое требование к качеству должно")
    deadline = _read_text(brief.get("deadline"), say, "Срок", "должен быть")

    budget = brief.get("budget_usd")
    if budget is not None and not _is_money(budget):
        say("Бюджет записан не как сумма: нужно одно число больше нуля (в долларах) "
            "или ничего, если бюджет ещё не согласован.")
        budget = None

    config = brief.get("config")
    if config is None:
        config = {}
    elif not isinstance(config, dict):
        say("Дополнительные настройки должны быть набором пар «имя: значение», "
            f"а не {_kind(config)}.")
        config = {}

    # ---- the stages
    raw_stages = brief.get("stages")
    if raw_stages is None or (_is_list(raw_stages) and not raw_stages):
        questions.append(_ASK_STAGES)
        stages_said = True
        raw_stages = []
    elif not _is_list(raw_stages):
        say(f"Этапы должны быть списком, где каждый этап описан отдельно, "
            f"а не {_kind(raw_stages)}.")
        stages_said = True
        raw_stages = []

    stages: list[_Stage] = []
    first_named: dict[str, int] = {}      # a title and the first stage that has it
    for n, raw in enumerate(raw_stages, 1):
        if not isinstance(raw, dict):
            say(f"Этап номер {n} описан неверно: каждый этап нужно описать отдельно, "
                f"с названием, файлами и условиями готовности, а не {_kind(raw)}.")
            continue
        stage = _Stage(n)
        name = raw.get("title")
        if name is None or (isinstance(name, str) and not name.strip()):
            stage.say(f"У этапа номер {n} нет названия: дайте каждому этапу своё название.")
        elif not isinstance(name, str):
            stage.say(f"Название этапа номер {n} должно быть одним текстом, "
                      f"а не {_kind(name)}.")
        else:
            stage.title = name.strip()
            first = first_named.setdefault(stage.title, n)
            if first != n:
                stage.say(f"Название «{stage.title}» использовано дважды: так названы "
                          f"этапы номер {first} и {n}. У каждого этапа должно быть "
                          "своё название.")
        stages.append(stage)
        in_stage, of_stage, the_stage = (f"этап{ending} {stage.stem}"
                                          for ending in ("е", "а", ""))

        stage.description = _read_text(raw.get("description"), stage.say,
                                       f"Описание {of_stage}")
        if stage.description is not None and \
                len(stage.description) > TEXT_LIMITS["description"]:
            stage.say(f"Описание {of_stage} длиннее {TEXT_LIMITS['description']} знаков: "
                      "сократите его.")

        key = raw.get("specialist")
        if key is None or (isinstance(key, str) and not key.strip()):
            key = DEFAULT_SPECIALIST        # not given: the default specialist
        elif not isinstance(key, str):
            stage.say(f"Специалист {of_stage} должен быть назван одним словом "
                      f"из списка специалистов, а не {_kind(key)}.")
            key = DEFAULT_SPECIALIST
        elif key.strip() not in SPECIALISTS:
            stage.say(f"В {in_stage} выбран специалист, которого нет в списке "
                      f"возможных («{key.strip()}»).")
            key = DEFAULT_SPECIALIST
        key = key.strip()
        stage.lens = SPECIALISTS[key]
        stage.role = _role(key)

        raw_writes = raw.get("writes")
        if raw_writes is None or (_is_list(raw_writes) and not raw_writes):
            stage.flagged = True
            ask_files.append(_ASK_WRITES.format(stage=in_stage))      # "на этапе «X»"
        stage.writes = _read_list(raw_writes, stage.say, f"В {in_stage} список файлов должен",
                                  f"В {in_stage} каждый файл должен")
        for file in stage.writes:
            if file.strip():
                fault = _path_fault(file)
                if fault:
                    stage.say(fault.format(file=file, stage=in_stage))

        raw_done = raw.get("done_when")
        if raw_done is None or (_is_list(raw_done) and not raw_done):
            stage.flagged = True
            ask_done.append(_ASK_DONE.format(stage=the_stage))
        stage.done_when = _read_list(raw_done, stage.say,
                                     f"В {in_stage} условия готовности должны",
                                     f"В {in_stage} каждое условие готовности должно")
        stage.after = _read_list(raw.get("after"), stage.say,
                                 f"В {in_stage} список этапов, после которых он "
                                 "начинается, должен",
                                 f"В {in_stage} каждый этап, после которого начинается "
                                 "этот, должен")
        stage.check = _read_text(raw.get("check"), stage.say,
                                 f"В {in_stage} команда для автоматической проверки",
                                 "должна быть")

    # ---- what each stage waits for, by title
    by_title: dict[str, _Stage] = {}
    for stage in stages:
        if stage.title:
            by_title.setdefault(stage.title, stage)
    for stage in stages:
        for other_name in dict.fromkeys(stage.after):
            if not other_name.strip():
                continue                    # a blank line: already said with the list
            other = by_title.get(other_name.strip())
            if other is None:
                stage.say(f"В этапе {stage.stem} сказано, что он идёт после этапа "
                          f"«{other_name.strip()}», но этапа с таким названием в плане нет. "
                          "Проверьте название или уберите его из списка.")
            elif other is stage:
                stage.say(f"Этап {stage.stem} не может ждать сам себя: так работа "
                          "никогда не начнётся.")
            elif other.n not in stage.waits_for:
                stage.waits_for.append(other.n)

    # ---- the specification: what the engine's own rules are asked about
    spec = _specification(title, goal, budget, config, stages)
    tasks = spec["tasks"]
    by_place = {stage.n: stage for stage in stages}

    # A circle of waiting: the work could never start. The engine's own
    # finder is asked again and again, a circle at a time, so that every
    # circle is said and not only the first.
    left = list(tasks)
    while True:
        circle = find_cycle(left)
        if not circle:
            break
        places = {_place(task_id) for task_id in circle}
        members = [by_place[place] for place in sorted(places)]
        for member in members:
            member.say(f"{_names(members)} ждут друг друга по кругу: так работа никогда "
                       "не начнётся. Укажите, какой из них идёт первым.")
        gone = set(circle)
        left = [task for task in left if task["id"] not in gone]

    # Stages that change one place and neither waits for the other: the
    # same rule and the same measure the engine applies. Each of them
    # hears it once, with the others named.
    clashes: dict[int, dict[int, set[str]]] = {}
    for overlap in find_write_overlaps(tasks, None):
        if not overlap.ordered:
            a, b = _place(overlap.task_a), _place(overlap.task_b)
            files = {overlap.path_a, overlap.path_b}
            clashes.setdefault(a, {}).setdefault(b, set()).update(files)
            clashes.setdefault(b, {}).setdefault(a, set()).update(files)
    for place in sorted(clashes):
        files: set[str] = set()
        for other_files in clashes[place].values():
            files.update(other_files)
        others = [by_place[other] for other in sorted(clashes[place])]
        by_place[place].say(_overlap_words(by_place[place], others, sorted(files)))

    # ---- the engine's own check: what it reports and nothing here did
    # is a problem too, in words, with its text kept beside them.
    try:
        errors = validate_team_spec(spec, Path("."), check_fs=False).errors
        extra = _unsaid(errors, stages, goal_said, title_said, stages_said)
    except Exception as exc:            # the answer must never be lost to a crash
        extra = [{"stage": None,
                  "what": "Система не смогла проверить план: руководитель команды должен "
                          "посмотреть подробности.",
                  "detail": f"{type(exc).__name__}: {exc}"}]

    # The request, then the stages, then the files of every stage, then
    # the end of every stage: the most important first.
    questions += ask_files + ask_done
    problems = [{"stage": None, "what": text} for text in whole]
    problems += [{"stage": stage.title, "what": text}
                 for stage in stages for text in stage.notes]
    problems += extra

    # The engine's configuration has the last word about the budget: the
    # plan shows the sum the engine will hold the team to.
    shown_budget = budget
    if "budget_usd" in config:
        shown_budget = config["budget_usd"] if _is_money(config["budget_usd"]) else None

    ok = not questions and not problems
    return {
        "ok": ok,
        "questions": list(questions),
        "question": questions[0] if questions else None,
        "problems": problems,
        "plan": _plan(title, goal, shown_budget, constraints, materials, deadline,
                      quality, stages),
        "spec": spec if ok else None,
    }
