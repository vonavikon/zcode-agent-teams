"""A brief in plain words becomes questions, a plan and a specification.

Unit tests for ``agent_teams.onboarding`` (v2.1 specification, section
14). What is pinned here: a valid brief becomes a specification the
engine itself validates and accepts in a real project; only the four
critical gaps are asked, one clear question at a time, the most
important first; everything else may be absent; a wrong brief gives
problems that name the stage and are never phrased as questions; the
words carry no long dash and no word the owner would not know.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent_teams import engine, onboarding, validation, viewmodel
from agent_teams.errors import UsageError
from agent_teams.onboarding import BriefError, review

REQUEST = ("Изучи рынок конкурентов, сравни их предложения и подготовь "
           "стратегию запуска продукта")
RESULT = "Отчёт со сравнением и стратегией запуска"
CHECK = "python -m doctest reports/competitors.md"

# The example of the brief, field by field.
FULL = {
    "title": "Сравнение конкурентов",
    "request": REQUEST,
    "result": RESULT,
    "constraints": ["только открытые источники"],
    "materials": ["docs/product.md"],
    "deadline": "к 1 декабря",
    "budget_usd": 10,
    "quality": ["каждый вывод со ссылкой на источник"],
    "stages": [
        {"title": "Собрать данные о конкурентах",
         "description": "Найти пять конкурентов и их предложения",
         "specialist": "analyst",
         "writes": ["reports/competitors.md"],
         "done_when": ["в таблице не меньше пяти конкурентов"],
         "check": CHECK},
        {"title": "Сравнить предложения", "specialist": "analyst",
         "writes": ["reports/comparison.md"],
         "done_when": ["есть таблица сравнения"],
         "after": ["Собрать данные о конкурентах"]},
        {"title": "Подготовить стратегию запуска", "specialist": "writer",
         "writes": ["reports/strategy.md"],
         "done_when": ["стратегия описывает первые шаги", "у стратегии есть сроки"],
         "after": ["Сравнить предложения"]},
    ],
    "config": {},
}

# Only a request and one stage that has files and done_when.
MINIMAL = {"request": "Подготовь отчёт",
           "stages": [{"title": "Собрать данные", "writes": ["reports/data.md"],
                       "done_when": ["данные собраны"]}]}

ANSWER_KEYS = ["ok", "questions", "question", "problems", "plan", "spec"]
PLAN_KEYS = ["title", "goal", "stage_count", "stages", "specialists", "budget_usd",
             "constraints", "materials", "deadline", "quality", "summary"]
STAGE_KEYS = ["title", "specialist", "after", "result", "done_when", "description"]

LONG_DASHES = ("\u2014", "\u2013")          # the em dash and the en dash
UNKNOWN_WORDS = ("spec", "json", "deps", "id")  # words the owner would not know

# The table of the specialists: the key, the name on the board, the lens.
SPECIALISTS = {
    "analyst": ("AI-аналитик", "behavior"),
    "writer": ("AI-редактор документации", "docs"),
    "engineer": ("AI-разработчик", "correctness"),
    "security": ("AI-специалист по безопасности", "security"),
    "performance": ("AI-аналитик производительности", "performance"),
    "specialist": ("AI-специалист", None),
}


# ---------------------------------------------------------------- helpers


def stage(title: str, number: int = 1, **fields):
    """One complete stage. ``number`` keeps the files of two stages apart."""
    out = {"title": title, "writes": [f"reports/part{number}.md"],
           "done_when": [f"часть {number} готова"]}
    out.update(fields)
    return out


def brief(stages=None, **fields):
    """A brief that is complete except for what a test changes."""
    out = {"title": "Моя команда", "request": "Подготовь отчёт",
           "stages": [stage("Собрать данные")] if stages is None else stages}
    out.update(fields)
    return out


def without(mapping: dict, *keys):
    """The mapping without some keys: a field that is absent."""
    out = dict(mapping)
    for key in keys:
        out.pop(key, None)
    return out


def strings(node):
    """Every string inside a nested structure, keys left out."""
    if isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)
    elif isinstance(node, str):
        yield node


def owner_words(answer: dict):
    """What the owner may read: the questions, the problems and the plan.

    The specification is the engine's format and ``detail`` is the
    engine's own text: neither is written for the owner.
    """
    yield from answer["questions"]
    for problem in answer["problems"]:
        yield problem["what"]
    yield from strings(answer["plan"])


def whats(answer: dict, title: str | None):
    """The problems about one stage (None: about the whole brief)."""
    return [problem["what"] for problem in answer["problems"] if problem["stage"] == title]


def kinds(questions: list[str]) -> list[str]:
    """Which gap each question is about, known by its own words."""
    out = []
    for text in questions:
        if "просите команду сделать" in text:
            out.append("request")
        elif "Из каких этапов" in text:
            out.append("stages")
        elif "файл" in text:
            out.append("files")
        elif "готов" in text:
            out.append("done")
        else:
            out.append("unknown: " + text)
    return out


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def init_git(root: Path) -> None:
    """A project with one commit, the way the dashboard tests make one."""
    (root / "out").mkdir()
    # Git keeps no empty directory; a worktree of the base commit needs "out".
    (root / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (root / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@e.i")
    git(root, "config", "user.name", "t")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")


def init_team(root: Path, spec: dict, team: str = "t") -> dict:
    """What ``team.py init`` does with a specification, in a real project."""
    path = root / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return engine.op_init(root, team, path)


# What is written wrongly. Each line: what is wrong, the brief, the stage
# (or stages) the problem is about, a word the problem must carry. None
# stands for the whole brief.
WRONG = [
    ("after names a stage that is not in the plan",
     brief(stages=[stage("Сбор", 1), stage("Отчёт", 2, after=["Такого этапа нет"])]),
     "Отчёт", "Такого этапа нет"),
    ("two stages wait for each other in a circle",
     brief(stages=[stage("Сбор", 1, after=["Отчёт"]), stage("Отчёт", 2, after=["Сбор"])]),
     ("Сбор", "Отчёт"), "по кругу"),
    ("three stages wait for each other in a circle",
     brief(stages=[stage("Сбор", 1, after=["Выводы"]),
                   stage("Отчёт", 2, after=["Сбор"]),
                   stage("Выводы", 3, after=["Отчёт"])]),
     ("Сбор", "Отчёт", "Выводы"), "по кругу"),
    ("a stage waits for itself",
     brief(stages=[stage("Сбор", 1, after=["Сбор"])]), "Сбор", "сам себя"),
    ("two stages write one file and neither waits for the other",
     brief(stages=[stage("Сбор", 1, writes=["reports/one.md"]),
                   stage("Отчёт", 2, writes=["reports/one.md"])]),
     ("Сбор", "Отчёт"), "reports/one.md"),
    ("a stage writes a folder another stage writes in",
     brief(stages=[stage("Сбор", 1, writes=["reports/"]),
                   stage("Отчёт", 2, writes=["reports/part2.md"])]),
     ("Сбор", "Отчёт"), "reports/"),
    ("a specialist key that is not in the table",
     brief(stages=[stage("Сбор", 1, specialist="enginer")]), "Сбор", "enginer"),
    ("a budget of zero", brief(budget_usd=0), None, "Бюджет"),
    ("a budget below zero", brief(budget_usd=-5), None, "Бюджет"),
    ("a budget written as text", brief(budget_usd="10"), None, "Бюджет"),
    ("a budget that is yes or no", brief(budget_usd=True), None, "Бюджет"),
    ("a title used by two stages",
     brief(stages=[stage("Сбор", 1), stage("Отчёт", 2), stage("Сбор", 3)]), "Сбор", "дважды"),
    ("a request longer than 1000 signs", brief(request="я" * 1001), None, "1000"),
    ("a stage description longer than 500 signs",
     brief(stages=[stage("Сбор", 1, description="Я" * 501)]), "Сбор", "500"),
    ("a title longer than 120", brief(title="Н" * 121), None, "120"),
    # a field of the wrong shape
    ("files written as one text",
     brief(stages=[stage("Сбор", 1, writes="reports/part1.md")]), "Сбор",
     "список файлов должен быть списком, а не одним текстом"),
    ("done_when written as one text",
     brief(stages=[stage("Сбор", 1, done_when="готово")]), "Сбор",
     "условия готовности должны быть списком, а не одним текстом"),
    ("after written as one text",
     brief(stages=[stage("Сбор", 1), stage("Отчёт", 2, after="Сбор")]), "Отчёт",
     "должен быть списком, а не одним текстом"),
    ("constraints written as one text", brief(constraints="только открытые источники"),
     None, "Ограничения должны быть списком, а не одним текстом"),
    ("materials written as one text", brief(materials="docs/product.md"), None,
     "Материалы должны быть списком, а не одним текстом"),
    ("quality written as one text", brief(quality="каждый вывод со ссылкой"), None,
     "Требования к качеству должны быть списком, а не одним текстом"),
    ("the title written as a list", brief(title=["Моя", "команда"]), None,
     "Название команды должно быть одним текстом, а не списком"),
    ("the request written as a list", brief(request=["Подготовь", "отчёт"]), None,
     "Просьба команде должна быть одним текстом, а не списком"),
    ("the stages written as one text", brief(stages="сделать всё"), None,
     "Этапы должны быть списком"),
    ("a stage written as one text",
     brief(stages=[stage("Сбор", 1), "Подготовить отчёт"]), None, "Этап номер 2 описан неверно"),
    ("a stage without a title",
     brief(stages=[without(stage("Сбор", 1), "title")]), None, "У этапа номер 1 нет названия"),
    ("a stage that is empty",
     brief(stages=[stage("Сбор", 1), None]), None, "а не пустотой"),
    ("a stage title written as a number",
     brief(stages=[{**stage("Сбор", 1), "title": 7}]), None,
     "Название этапа номер 1 должно быть одним текстом, а не числом"),
    ("a stage description written as a list",
     brief(stages=[stage("Сбор", 1, description=["Найти"])]), "Сбор",
     "Описание этапа «Сбор» должно быть одним текстом, а не списком"),
    ("a specialist written as a number",
     brief(stages=[stage("Сбор", 1, specialist=5)]), "Сбор",
     "Специалист этапа «Сбор» должен быть назван одним словом"),
    ("a check written as a list",
     brief(stages=[stage("Сбор", 1, check=[CHECK])]), "Сбор",
     "команда для автоматической проверки должна быть одним текстом"),
    ("the deadline written as a number", brief(deadline=12), None, "Срок должен быть"),
    ("the result written as a number", brief(result=5), None,
     "Описание ожидаемого результата должно быть одним текстом"),
    ("the config written as a list", brief(config=["workspace"]), None,
     "Дополнительные настройки должны быть набором пар"),
    ("a line that is not a text among the files",
     brief(stages=[stage("Сбор", 1, writes=["reports/part1.md", 5])]), "Сбор",
     "каждый файл должен быть одним текстом"),
    ("an empty line among the conditions",
     brief(stages=[stage("Сбор", 1, done_when=["готово", "  "])]), "Сбор",
     "каждое условие готовности должно быть одним текстом"),
    ("a line that is not a text among the constraints", brief(constraints=["ок", None]),
     None, "Каждое ограничение должно быть одним текстом"),
]
WRONG += [
    (f"the file {path}", brief(stages=[stage("Сбор", 1, writes=[path])]), "Сбор", path)
    for path in ("/etc/passwd", "../secret.md", "docs/../secret.md", ".agent-teams/m5/state.db",
                 ".git/config", "C:\\docs\\report.md", "D:/report.md", "~/report.md")
]

# Briefs no person would write, but mappings: the answer must still be
# an answer. What is not a mapping at all is in NOT_A_MAPPING.
GARBAGE = [
    {},
    {"request": object(), "stages": object(), "budget_usd": object(), "title": b"x"},
    {"request": {"a": 1}, "result": ["x"], "deadline": {"d": 1}, "config": {1: "a", "b": 2}},
    {"stages": [[]], "request": "x"},
    {"stages": [5, None, [], "text"], "request": "x"},
    {"stages": [{"title": {"x": 1}, "writes": {"x": 1}, "done_when": {"x": 1}}]},
    {"stages": [{"title": "A", "writes": [["a"]], "done_when": [[1]], "after": [{"A": 1}],
                 "specialist": {"a": 1}, "check": {"cmd": 1}, "description": b"d"}]},
    {"stages": [{"title": "A", "writes": ["a.md"], "done_when": ["ок"],
                 "after": ["A", "B"], "specialist": ["analyst"]}]},
    {"stages": [{"title": "A", "writes": [None, ("x",)], "done_when": [None], "title2": 3}]},
    {"budget_usd": {"usd": 10}, "budget_usd2": [10], "constraints": [["x"]], "materials": {1: [2]}},
    {"budget_usd": float("nan")},
    {"budget_usd": 10 ** 400},
    {"request": "x" * 5000, "result": "y" * 5000, "title": "T" * 5000,
     "stages": [{"title": "T" * 5000, "description": "d" * 5000, "writes": ["a"],
                 "done_when": ["b"]}]},
    {1: "x", None: 2, "request": "ok", "title": False, "deadline": True,
     "stages": [{1: "x", "title": "A", "writes": ["a.md"], "done_when": ["ок"], 2.5: 3}]},
]
_LOOP = {"request": "x"}
_LOOP["stages"] = [_LOOP]
GARBAGE.append(_LOOP)
NOT_A_MAPPING = [None, [], "text", 5, True, (), set(), frozenset({1})]


# ---------------------------------------------------------------- a full brief


class TestAFullValidBrief:
    def test_it_is_ok_and_nothing_is_asked_or_reported(self):
        answer = review(FULL)
        assert list(answer) == ANSWER_KEYS
        assert answer["ok"] is True
        assert answer["questions"] == [] and answer["question"] is None
        assert answer["problems"] == []

    def test_the_answer_has_its_six_parts_with_their_types(self):
        answer = review(FULL)
        assert isinstance(answer["ok"], bool)
        assert isinstance(answer["questions"], list)
        assert answer["question"] is None or isinstance(answer["question"], str)
        assert isinstance(answer["problems"], list)
        assert isinstance(answer["plan"], dict) and list(answer["plan"]) == PLAN_KEYS
        assert isinstance(answer["spec"], dict)

    def test_the_specification_is_the_one_the_brief_describes(self):
        spec = review(FULL)["spec"]
        assert list(spec) == ["title", "goal", "config", "agents", "tasks"]
        assert spec["title"] == "Сравнение конкурентов"
        assert spec["goal"] == REQUEST + "\n" + "Ожидаемый результат: " + RESULT
        assert spec["config"] == {"budget_usd": 10}
        assert spec["agents"] == [{"id": "spec-1", "role": "executor"},
                                  {"id": "spec-2", "role": "executor"},
                                  {"id": "spec-3", "role": "executor"}]
        first, second, third = spec["tasks"]
        assert list(first) == ["id", "title", "description", "assignee", "lens",
                               "files_write", "acceptance", "deps"]
        assert first == {
            "id": "stage-1", "title": "Собрать данные о конкурентах",
            "description": "Найти пять конкурентов и их предложения",
            "assignee": "spec-1", "lens": "behavior",
            "files_write": ["reports/competitors.md"],
            "acceptance": [
                {"id": "AC-001", "description": "в таблице не меньше пяти конкурентов",
                 "type": "manual"},
                {"id": "AC-002", "description": "команда проходит: " + CHECK,
                 "type": "test", "verification": CHECK}],
            "deps": []}
        assert second["acceptance"] == [{"id": "AC-001", "description": "есть таблица сравнения",
                                         "type": "manual"}]
        assert third["lens"] == "docs" and third["assignee"] == "spec-3"
        assert (first["deps"], second["deps"], third["deps"]) == ([], ["stage-1"], ["stage-2"])
        assert "description" not in second and "description" not in third

    def test_it_passes_the_engine_own_validation_of_the_real_project(self, tmp_path):
        result = validation.validate_team_spec(review(FULL)["spec"], tmp_path)
        assert result.ok, [issue.message for issue in result.errors]

    def test_the_engine_accepts_it_at_init_in_a_real_project(self, tmp_path):
        init_git(tmp_path)
        made = init_team(tmp_path, review(FULL)["spec"])
        assert made["team"] == "t" and made["tasks"] == 3
        mirror = json.loads((tmp_path / ".agent-teams" / "t" / "tasks.json")
                            .read_text(encoding="utf-8"))
        assert [task["id"] for task in mirror["tasks"]] == ["stage-1", "stage-2", "stage-3"]
        assert [task["deps"] for task in mirror["tasks"]] == [[], ["stage-1"], ["stage-2"]]

    def test_unknown_fields_are_ignored(self):
        answer = review({**FULL, "notes": "говорили по телефону", "priority": 3,
                         "stages": [{**FULL["stages"][0], "comment": "уточнить у владельца"},
                                    *FULL["stages"][1:]]})
        assert answer["ok"] is True

    def test_the_result_is_added_to_the_goal_only_when_given(self):
        assert review(without(FULL, "result"))["spec"]["goal"] == REQUEST
        assert review({**FULL, "result": "   "})["spec"]["goal"] == REQUEST


# ---------------------------------------------------------------- a minimal brief


class TestAMinimalBrief:
    def test_a_request_and_one_stage_are_enough(self):
        answer = review(MINIMAL)
        assert answer["ok"] is True and answer["question"] is None
        assert answer["spec"]["config"] == {}
        assert "budget_usd" not in answer["spec"]["config"]
        assert answer["spec"]["title"] == "Подготовь отчёт"      # no title: the request
        assert answer["spec"]["tasks"][0] == {
            "id": "stage-1", "title": "Собрать данные", "assignee": "spec-1",
            "files_write": ["reports/data.md"],
            "acceptance": [{"id": "AC-001", "description": "данные собраны",
                            "type": "manual"}],
            "deps": []}

    def test_the_budget_is_none_and_the_summary_says_it_is_not_agreed(self):
        plan = review(MINIMAL)["plan"]
        assert plan["budget_usd"] is None
        assert "бюджет не согласован" in plan["summary"]

    def test_nothing_asks_for_the_date_the_quality_or_the_constraints(self):
        answer = review(MINIMAL)
        assert answer["questions"] == [] and answer["question"] is None
        plan = answer["plan"]
        assert plan["deadline"] is None
        assert plan["constraints"] == [] and plan["quality"] == [] and plan["materials"] == []
        assert "ограничений нет" in plan["summary"]

    @pytest.mark.parametrize("absent", ["result", "constraints", "materials", "deadline",
                                        "budget_usd", "quality", "config"])
    def test_a_field_of_the_brief_may_be_absent_and_is_never_asked_for(self, absent):
        answer = review(without(FULL, absent))
        assert answer["questions"] == [], absent
        assert answer["ok"] is True, [problem["what"] for problem in answer["problems"]]
        # The budget stays where it was put; only its own absence removes it.
        assert answer["spec"]["config"] == ({} if absent == "budget_usd" else {"budget_usd": 10})

    @pytest.mark.parametrize("absent", ["description", "specialist", "after", "check"])
    def test_a_field_of_a_stage_may_be_absent_and_is_never_asked_for(self, absent):
        stages = [{key: value for key, value in item.items() if key != absent}
                  for item in FULL["stages"]]
        answer = review({**FULL, "stages": stages})
        assert answer["questions"] == [], absent
        assert answer["ok"] is True, [problem["what"] for problem in answer["problems"]]

    def test_a_blank_optional_value_is_as_good_as_an_absent_one(self):
        answer = review({**MINIMAL, "title": " ", "result": "", "deadline": "  ",
                         "constraints": [], "budget_usd": None})
        assert answer["ok"] is True and answer["questions"] == []
        plan = answer["plan"]
        assert plan["title"] == MINIMAL["request"]
        assert plan["deadline"] is None and plan["budget_usd"] is None


# ---------------------------------------------------------------- the four gaps


class TestOnlyTheFourCriticalGapsAreAsked:
    def test_no_request(self):
        answer = review(without(MINIMAL, "request"))
        assert answer["questions"] == ["Что вы просите команду сделать?"]
        assert answer["question"] == "Что вы просите команду сделать?"
        assert answer["problems"] == [] and answer["spec"] is None

    def test_a_blank_request_is_no_request(self):
        answer = review({**MINIMAL, "request": "  "})
        assert kinds(answer["questions"]) == ["request"]

    def test_no_stages_at_all(self):
        for stages in (None, []):
            answer = review({**MINIMAL, "stages": stages})
            assert len(answer["questions"]) == 1, stages
            assert kinds(answer["questions"]) == ["stages"]
            assert answer["problems"] == []

    def test_a_stage_without_writes(self):
        answer = review(brief(stages=[without(stage("Собрать данные"), "writes")]))
        [question] = answer["questions"]
        assert "«Собрать данные»" in question and "файл" in question
        assert answer["problems"] == []

    def test_an_empty_list_of_writes_is_no_writes(self):
        answer = review(brief(stages=[stage("Собрать данные", writes=[])]))
        assert kinds(answer["questions"]) == ["files"]

    def test_a_stage_without_done_when(self):
        answer = review(brief(stages=[without(stage("Собрать данные"), "done_when")]))
        [question] = answer["questions"]
        assert "«Собрать данные»" in question and "готов" in question
        assert answer["problems"] == []

    def test_an_empty_list_of_conditions_is_no_done_when(self):
        answer = review(brief(stages=[stage("Собрать данные", done_when=[])]))
        assert kinds(answer["questions"]) == ["done"]

    def test_each_gap_gives_exactly_one_question_in_the_order_of_the_brief(self):
        one = review({"stages": [{"title": "Сбор"}, {"title": "Отчёт"}]})
        assert kinds(one["questions"]) == ["request", "files", "files", "done", "done"]
        mixed = review({"request": "Подготовь отчёт",
                        "stages": [{"title": "Сбор", "writes": ["reports/1.md"]},
                                   {"title": "Отчёт", "done_when": ["готово"]}]})
        assert kinds(mixed["questions"]) == ["files", "done"]
        only_request = review(without(MINIMAL, "request"))
        assert kinds(only_request["questions"]) == ["request"]

    def test_the_most_important_first_one_question_at_a_time(self):
        answer = review({**without(MINIMAL, "request", "stages"),
                         "stages": [{"title": "Сбор"}, {"title": "Отчёт"}]})
        assert answer["question"] == answer["questions"][0] == "Что вы просите команду сделать?"
        # Five questions, and no two alike: each names the stage it is about.
        assert len(answer["questions"]) == 5 and len(set(answer["questions"])) == 5
        for text in answer["questions"]:
            assert text.count("?") == 1 and text.strip().endswith((".", "?")), text

    def test_a_question_names_an_untitled_stage_by_its_place(self):
        answer = review({"request": "Подготовь отчёт", "stages": [{"writes": []}]})
        assert kinds(answer["questions"]) == ["files", "done"]
        assert all("этап номер 1" in text for text in answer["questions"])

    def test_nothing_is_ok_while_a_question_is_left(self):
        for left in (without(MINIMAL, "request"), {"request": "x", "stages": []}):
            answer = review(left)
            assert answer["ok"] is False and answer["spec"] is None


# ---------------------------------------------------------------- what is wrong


class TestWhatIsWrittenWrongly:
    @pytest.mark.parametrize("wrong, given, about, needle", WRONG,
                             ids=[wrong[0] for wrong in WRONG])
    def test_it_is_a_problem_that_names_the_stage(self, wrong, given, about, needle):
        answer = review(given)
        assert answer["ok"] is False, wrong
        assert answer["spec"] is None, wrong
        assert answer["questions"] == [], wrong          # what is wrong is never asked for
        titles = about if isinstance(about, tuple) else (about,)
        for title in titles:
            found = whats(answer, title)
            assert found, (wrong, title, answer["problems"])
            assert any(needle in text for text in found), (wrong, needle, found)

    def test_every_problem_names_a_stage_or_the_whole_brief(self):
        for wrong, given, _about, _needle in WRONG:
            for problem in review(given)["problems"]:
                assert list(problem) == ["stage", "what"], (wrong, problem)
                assert problem["stage"] is None or isinstance(problem["stage"], str)
                assert problem["what"].strip() == problem["what"] and problem["what"]

    def test_a_clash_names_the_stage_it_is_about_and_the_other_one(self):
        made = [stage("Сбор", 1, writes=["reports/one.md"]),
                stage("Отчёт", 2, writes=["reports/one.md"])]
        answer = review(brief(stages=made))
        assert [problem["stage"] for problem in answer["problems"]] == ["Сбор", "Отчёт"]
        [first] = whats(answer, "Сбор")
        assert first == ("Этап «Сбор» меняет файл «reports/one.md», который меняют ещё этап «Отчёт», "
                         "но не сказано, какой из них идёт раньше: один может затереть работу другого. "
                         "Укажите порядок этапов или доверьте это место только одному из них.")
        [second] = whats(answer, "Отчёт")
        assert second.startswith("Этап «Отчёт» меняет файл «reports/one.md»")
        assert "этап «Сбор»" in second

    def test_every_circle_is_said_and_names_only_its_own_members(self):
        made = [stage("Сбор", 1, after=["Отчёт"]), stage("Отчёт", 2, after=["Сбор"]),
                stage("Проверка", 3, after=["Выводы"]), stage("Выводы", 4, after=["Проверка"])]
        answer = review(brief(stages=made))
        assert answer["ok"] is False
        assert [problem["stage"] for problem in answer["problems"]] == [
            "Сбор", "Отчёт", "Проверка", "Выводы"]
        # Each circle is said once for each of its members, in the order of
        # the brief, and names nobody from the other circle.
        for members in (("Сбор", "Отчёт"), ("Проверка", "Выводы")):
            first, second = members
            for title in members:
                [text] = whats(answer, title)
                assert text == (f"Этап «{first}» и этап «{second}» ждут друг друга по кругу: "
                                "так работа никогда не начнётся. Укажите, какой из них идёт первым.")

    def test_a_circle_of_three_is_named_in_the_order_of_the_brief(self):
        made = [stage("Сбор", 1, after=["Выводы"]), stage("Отчёт", 2, after=["Сбор"]),
                stage("Выводы", 3, after=["Отчёт"])]
        [text] = whats(review(brief(stages=made)), "Отчёт")
        assert text.startswith("Этап «Сбор», этап «Отчёт» и этап «Выводы» ждут друг друга по кругу")

    def test_a_stage_with_two_problems_hears_both(self):
        answer = review(brief(stages=[stage("Сбор", 1, specialist="enginer", writes="../x.md")]))
        assert len(whats(answer, "Сбор")) == 2

    def test_no_problem_is_ever_phrased_as_a_question(self):
        for wrong, given, _about, _needle in WRONG:
            for problem in review(given)["problems"]:
                text = problem["what"]
                assert not text.rstrip().endswith("?") and "?" not in text, (wrong, text)

    def test_no_problem_is_a_path_or_a_traceback(self):
        technical = re.compile(r"tasks\[|stages\[\w+\]|\bE_[A-Z_]+\b|Traceback|Error:|\w+\[\d+\]")
        for wrong, given, _about, _needle in WRONG:
            for problem in review(given)["problems"]:
                assert not technical.search(problem["what"]), (wrong, problem["what"])

    def test_a_stage_may_change_a_file_another_stage_changes_if_they_are_ordered(self):
        first = stage("Сбор", 1, writes=["reports/one.md"])
        same = {"writes": ["reports/one.md"]}
        ordered = review(brief(stages=[first, stage("Отчёт", 2, after=["Сбор"], **same)]))
        assert ordered["ok"] is True, [problem["what"] for problem in ordered["problems"]]
        through = review(brief(stages=[
            first, stage("Проверка", 2, after=["Сбор"]),
            stage("Отчёт", 3, after=["Проверка"], **same)]))
        assert through["ok"] is True, [problem["what"] for problem in through["problems"]]

    def test_a_file_the_engine_accepts_is_accepted(self):
        answer = review(brief(stages=[stage("Сбор", 1, writes=[
            "docs\\report.md", "./docs/notes.md", "reports/", ".github/workflows/ci.yml"])]))
        assert answer["ok"] is True, [problem["what"] for problem in answer["problems"]]

    def test_the_same_limit_is_applied_to_the_goal_with_the_result(self):
        # The request alone fits in 1000 signs; with the line of the result
        # the goal is 1001 signs, and 1000 is still fine.
        over = brief(request="я" * 960, result="р" * 19)
        fits = brief(request="я" * 959, result="р" * 19)
        assert [len(review(given)["plan"]["goal"]) for given in (fits, over)] == [1000, 1001]
        [problem] = review(over)["problems"]
        assert "ожидаемым результатом" in problem["what"] and "1000" in problem["what"]
        assert review(fits)["problems"] == [] and review(fits)["ok"] is True

    def test_a_title_of_exactly_120_is_fine_and_121_is_not(self):
        assert review(brief(title="Н" * 120))["ok"] is True
        assert review(brief(title="Н" * 121))["ok"] is False


PATHS = [
    "/etc/passwd", "C:\\docs\\a.md", "D:/x.md", "../x.md", "docs/../x.md", ".agent-teams/m5/x",
    ".git/config", ".git", ".agent-teams", "~/x.md", "//server/share", "a//b", "", ".",
    "./", "a\nb", "x/", "docs\\a.md", "./docs/a.md", "docs/a.md", "a/./b.md",
    ".github/workflows/ci.yml", "отчёт/итоги.md",
]


@pytest.mark.parametrize("path", PATHS, ids=repr)
def test_the_files_follow_the_engine_own_path_rule(path):
    """The rule is not written a second time: what the engine refuses is a
    problem about the stage, and what it accepts is accepted."""
    try:
        validation.normalize_path(Path("."), path, check_fs=False)
    except UsageError:
        refused = True
    else:
        refused = False
    answer = review(brief(stages=[stage("Сбор", 1, writes=[path])]))
    assert (answer["ok"] is False) is refused, (path, answer["problems"])
    assert bool(whats(answer, "Сбор")) is refused, (path, answer["problems"])


@pytest.mark.parametrize("path, words", [
    ("/etc/passwd", "от начала диска"),
    ("C:\\docs\\report.md", "от начала диска"),
    ("../secret.md", "«..»"),
    ("docs/../secret.md", "«..»"),
    (".agent-teams/m5/state.db", "служебной папке"),
    (".git/config", "служебной папке"),
    ("a//b", "не примет"),
])
def test_a_file_outside_the_project_is_said_in_words(path, words):
    answer = review(brief(stages=[stage("Сбор", 1, writes=[path])]))
    [text] = whats(answer, "Сбор")
    assert words in text and f"«{path}»" in text and "в этапе «Сбор»" in text


# ---------------------------------------------------------------- order and after


class TestTheOrderAndTheWaiting:
    def test_the_order_of_the_stages_and_after_become_identifiers_and_dependencies(self):
        stages = [stage("Первый", 1),
                  stage("Третий", 3, after=["Второй"]),
                  stage("Второй", 2, after=["Первый"]),
                  stage("Четвёртый", 4, after=["Третий", "Первый"])]
        spec = review(brief(stages=stages))["spec"]
        # The identifiers follow the order of the brief, whatever language
        # the titles are in, not the order of the work.
        assert [(task["id"], task["title"]) for task in spec["tasks"]] == [
            ("stage-1", "Первый"), ("stage-2", "Третий"),
            ("stage-3", "Второй"), ("stage-4", "Четвёртый")]
        assert [task["deps"] for task in spec["tasks"]] == [
            [], ["stage-3"], ["stage-1"], ["stage-2", "stage-1"]]
        assert all(validation.NAME_RE.match(name) for task in spec["tasks"]
                   for name in [task["id"], task["assignee"], *task["deps"]])
        assert spec["agents"] == [{"id": f"spec-{number}", "role": "executor"}
                                  for number in range(1, 5)]
        assert [task["assignee"] for task in spec["tasks"]] == [f"spec-{number}" for number in range(1, 5)]

    def test_the_engine_accepts_it_and_keeps_the_dependencies(self, tmp_path):
        stages = [stage("Первый", 1),
                  stage("Третий", 3, after=["Второй"]),
                  stage("Второй", 2, after=["Первый"])]
        init_git(tmp_path)
        assert init_team(tmp_path, review(brief(stages=stages))["spec"])["tasks"] == 3
        mirror = json.loads((tmp_path / ".agent-teams" / "t" / "tasks.json")
                            .read_text(encoding="utf-8"))
        assert [(task["id"], task["deps"]) for task in mirror["tasks"]] == [
            ("stage-1", []), ("stage-2", ["stage-3"]), ("stage-3", ["stage-1"])]

    def test_waiting_twice_for_one_stage_is_one_dependency(self):
        spec = review(brief(stages=[stage("Сбор", 1), stage("Отчёт", 2, after=["Сбор", " Сбор "])]))["spec"]
        assert spec["tasks"][1]["deps"] == ["stage-1"]

    def test_the_plan_shows_the_stages_in_the_order_of_the_brief_with_what_they_wait_for(self):
        plan = review(FULL)["plan"]
        assert [list(item) for item in plan["stages"]] == [STAGE_KEYS] * 3
        assert plan["stages"][0] == {
            "title": "Собрать данные о конкурентах", "specialist": "AI-аналитик", "after": [],
            "result": ["reports/competitors.md"],
            "done_when": ["в таблице не меньше пяти конкурентов"],
            "description": "Найти пять конкурентов и их предложения"}
        assert plan["stages"][1]["after"] == ["Собрать данные о конкурентах"]
        assert plan["stages"][1]["description"] is None
        assert plan["stages"][2]["after"] == ["Сравнить предложения"]
        assert plan["stage_count"] == 3


# ---------------------------------------------------------------- the check


class TestTheCheck:
    def test_a_stage_with_check_gets_a_criterion_the_machine_runs(self):
        answer = review(brief(stages=[stage("Сбор", 1, check=CHECK)]))
        assert answer["spec"]["tasks"][0]["acceptance"] == [
            {"id": "AC-001", "description": "часть 1 готова", "type": "manual"},
            {"id": "AC-002", "description": "команда проходит: " + CHECK,
             "type": "test", "verification": CHECK}]

    def test_the_criterion_of_the_check_comes_after_all_the_conditions(self):
        answer = review(brief(stages=[stage("Сбор", 1, done_when=["первое", "второе", "третье"],
                                            check=CHECK)]))
        assert answer["spec"]["tasks"][0]["acceptance"] == [
            {"id": "AC-001", "description": "первое", "type": "manual"},
            {"id": "AC-002", "description": "второе", "type": "manual"},
            {"id": "AC-003", "description": "третье", "type": "manual"},
            {"id": "AC-004", "description": "команда проходит: " + CHECK,
             "type": "test", "verification": CHECK}]

    def test_without_a_check_there_is_no_criterion_of_the_machine(self):
        answer = review(MINIMAL)
        assert [item["type"] for item in answer["spec"]["tasks"][0]["acceptance"]] == ["manual"]

    def test_the_engine_normalizes_the_criteria_itself(self):
        spec = review(FULL)["spec"]
        assert [item["id"] for item in validation.normalize_acceptance(
            spec["tasks"][0]["acceptance"])] == ["AC-001", "AC-002"]


# ---------------------------------------------------------------- the words


def every_brief():
    yield "full", FULL
    yield "minimal", MINIMAL
    for wrong, given, _about, _needle in WRONG:
        yield wrong, given
    for number, given in enumerate(GARBAGE):
        yield f"garbage {number}", given


class TestTheWords:
    @pytest.mark.parametrize("name, given", list(every_brief()),
                             ids=[name for name, _ in every_brief()])
    def test_they_carry_no_long_dash_and_no_word_the_owner_would_not_know(self, name, given):
        for text in owner_words(review(given)):
            assert not any(dash in text for dash in LONG_DASHES), (name, text)
            lowered = text.lower()
            assert not any(word in lowered for word in UNKNOWN_WORDS), (name, text)

    def test_the_questions_are_the_questions_and_the_problems_are_not(self):
        answer = review({"stages": [{"title": "Сбор", "writes": []},
                                    {"title": "Отчёт", "specialist": "enginer",
                                     "done_when": ["готово"]}]})
        assert kinds(answer["questions"]) == ["request", "files", "files", "done"]
        assert [text.count("?") for text in answer["questions"]] == [1, 1, 1, 1]
        assert [problem["stage"] for problem in answer["problems"]] == ["Отчёт"]
        assert all("?" not in problem["what"] for problem in answer["problems"])

    def test_the_engine_text_is_kept_apart_from_the_words(self):
        answer = review(brief(config={"heartbeat_timeout": 60}))
        [problem] = answer["problems"]
        assert problem["detail"] == "config has unknown keys: heartbeat_timeout"
        assert problem["detail"] not in problem["what"]

    def test_no_long_dash_in_any_string_of_the_module(self):
        source = Path(onboarding.__file__).read_text(encoding="utf-8")
        assert not any(dash in source for dash in LONG_DASHES)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(dash in node.value for dash in LONG_DASHES)


# ---------------------------------------------------------------- not a dict


@pytest.mark.parametrize("given", NOT_A_MAPPING, ids=repr)
def test_a_brief_that_is_not_a_dict_raises_BriefError(given):
    with pytest.raises(BriefError):
        review(given)


def test_BriefError_is_a_ValueError_and_is_all_it_raises():
    assert issubclass(BriefError, ValueError)
    for given in GARBAGE:            # a dict never raises, whatever is inside
        answer = review(given)
        assert list(answer) == ANSWER_KEYS
        assert answer["ok"] in (True, False)
        assert answer["spec"] is None or answer["ok"] is True


# ---------------------------------------------------------------- the plan


class TestThePlan:
    def test_it_is_built_from_what_the_brief_has(self):
        plan = review(FULL)["plan"]
        assert plan["title"] == "Сравнение конкурентов"
        assert plan["goal"] == REQUEST + "\nОжидаемый результат: " + RESULT
        assert plan["specialists"] == [{"name": "AI-аналитик", "stages": 2},
                                       {"name": "AI-редактор документации", "stages": 1}]
        assert plan["budget_usd"] == 10
        assert plan["constraints"] == ["только открытые источники"]
        assert plan["materials"] == ["docs/product.md"]
        assert plan["deadline"] == "к 1 декабря"
        assert plan["quality"] == ["каждый вывод со ссылкой на источник"]

    def test_the_summary_tells_how_many_specialists_how_many_stages_the_budget_and_the_constraints(self):
        summary = review(FULL)["plan"]["summary"]
        assert "2 специалиста" in summary and "3 этапа" in summary
        assert "10 долларов" in summary and "только открытые источники" in summary
        assert summary.count(".") == 1                  # one or two sentences, not more

    @pytest.mark.parametrize("count, specialists, stages, money", [
        (1, "1 специалист", "1 этап", "1 доллар"),
        (2, "2 специалиста", "2 этапа", "2 доллара"),
        (5, "5 специалистов", "5 этапов", "5 долларов"),
        (11, "5 специалистов", "11 этапов", "11 долларов"),     # the endings of 11 to 14
        (12, "5 специалистов", "12 этапов", "12 долларов"),
        (14, "5 специалистов", "14 этапов", "14 долларов"),
        (21, "5 специалистов", "21 этап", "21 доллар"),
        (22, "5 специалистов", "22 этапа", "22 доллара"),
        (101, "5 специалистов", "101 этап", "101 доллар"),
    ])
    def test_the_counts_have_the_right_endings(self, count, specialists, stages, money):
        keys = list(SPECIALISTS)
        made = [stage(f"Этап {number}", number, specialist=keys[number % 5])
                for number in range(1, count + 1)]
        answer = review({"request": "Подготовь отчёт", "budget_usd": count, "stages": made})
        assert answer["ok"] is True, [problem["what"] for problem in answer["problems"]]
        summary = answer["plan"]["summary"]
        assert summary == f"В работе {specialists} и {stages}, бюджет {money}, ограничений нет."
        assert sum(item["stages"] for item in answer["plan"]["specialists"]) == count
        assert len(answer["plan"]["specialists"]) == min(count, 5)

    def test_a_sum_with_cents_takes_the_ending_of_a_fraction(self):
        assert "1.5 доллара" in review(brief(budget_usd=1.5))["plan"]["summary"]

    def test_the_plan_is_there_even_for_a_brief_that_cannot_start(self):
        plan = review({})["plan"]
        assert list(plan) == PLAN_KEYS
        assert plan["stage_count"] == 0 and plan["stages"] == []
        assert plan["specialists"] == [] and plan["budget_usd"] is None
        assert plan["deadline"] is None and plan["constraints"] == []
        assert "0 специалистов и 0 этапов" in plan["summary"]

    def test_a_stage_without_a_title_has_no_title_in_the_plan_and_is_named_by_its_place(self):
        answer = review({"request": "Подготовь отчёт",
                         "stages": [{"writes": ["reports/1.md"], "description": "Найти данные",
                                     "done_when": ["готово"], "after": ["Кого нет"]}]})
        [first] = answer["plan"]["stages"]
        assert first == {"title": None, "specialist": "AI-специалист", "after": ["Кого нет"],
                         "result": ["reports/1.md"], "done_when": ["готово"],
                         "description": "Найти данные"}
        assert [problem["stage"] for problem in answer["problems"]] == [None, None]
        assert "У этапа номер 1 нет названия" in answer["problems"][0]["what"]
        assert "В этапе номер 1 сказано" in answer["problems"][1]["what"]

    def test_a_stage_that_is_not_described_is_left_out_of_the_plan(self):
        answer = review({"request": "Подготовь отчёт", "stages": [stage("Сбор", 1), "Подготовить отчёт"]})
        assert answer["plan"]["stage_count"] == 1
        assert [item["title"] for item in answer["plan"]["stages"]] == ["Сбор"]

    def test_the_name_of_the_team_falls_back_to_the_request_and_then_to_a_default(self):
        assert review(without(MINIMAL, "title"))["spec"]["title"] == "Подготовь отчёт"
        long = review({**MINIMAL, "request": "Слово " * 60})["spec"]["title"]
        assert long == ("Слово " * 60)[:120] and len(long) == 120
        assert review(without(MINIMAL, "title", "request"))["spec"] is None   # not ready
        nameless = review({"request": " ", "stages": MINIMAL["stages"]})
        assert nameless["plan"]["title"] == "AI-команда"

    @pytest.mark.parametrize("key", sorted(SPECIALISTS))
    def test_the_specialist_has_the_name_the_board_gives(self, key):
        name, lens = SPECIALISTS[key]
        answer = review(brief(stages=[stage("Сбор", 1, specialist=key)]))
        task = answer["spec"]["tasks"][0]
        assert answer["plan"]["stages"][0]["specialist"] == name
        assert answer["plan"]["specialists"] == [{"name": name, "stages": 1}]
        assert task.get("lens") == lens
        assert ("lens" in task) is (lens is not None)
        # The same name the board gives a task with that lens.
        expected = viewmodel.role_name({"lens": lens}) if lens else viewmodel.role_name({})
        assert name == expected

    def test_the_table_of_specialists_is_the_one_of_the_brief(self):
        assert onboarding.SPECIALISTS == {key: lens for key, (_name, lens) in SPECIALISTS.items()}

    def test_the_default_specialist_works_without_a_lens(self):
        task = review(MINIMAL)["spec"]["tasks"][0]
        assert "lens" not in task
        assert review(MINIMAL)["plan"]["stages"][0]["specialist"] == "AI-специалист"

    def test_one_specialist_counts_the_stages_that_are_his(self):
        made = [stage("Первый", 1, specialist="engineer"), stage("Второй", 2, specialist="analyst"),
                stage("Третий", 3, specialist="engineer")]
        assert review(brief(stages=made))["plan"]["specialists"] == [
            {"name": "AI-разработчик", "stages": 2}, {"name": "AI-аналитик", "stages": 1}]


# ---------------------------------------------------------------- config


class TestAListFromPython:
    """The brief is read from a file as lists, but a caller may hand over tuples."""

    def test_a_tuple_is_read_as_a_list(self):
        made = {"request": "Подготовь отчёт",
                "constraints": ("только открытые источники",),
                "stages": ({"title": "Сбор", "writes": ("reports/1.md",),
                            "done_when": ("готово",), "after": ()},)}
        answer = review(made)
        assert answer["ok"] is True, answer["problems"]
        assert answer["spec"]["tasks"][0]["files_write"] == ["reports/1.md"]
        assert answer["plan"]["constraints"] == ["только открытые источники"]

    def test_something_that_is_not_a_list_at_all_is_a_problem(self):
        answer = review(brief(stages={"Сбор": "сделать"}))
        [problem] = answer["problems"]
        assert problem["stage"] is None and "Этапы должны быть списком" in problem["what"]


class TestTheConfig:
    def test_it_is_merged_as_it_is(self):
        answer = review(brief(config={"max_verification_failures": 3, "workspace": "worktree"}))
        assert answer["ok"] is True
        assert answer["spec"]["config"] == {"max_verification_failures": 3, "workspace": "worktree"}

    def test_the_budget_goes_in_and_the_config_has_the_last_word(self):
        both = review(brief(budget_usd=10, config={"budget_usd": 20}))
        assert both["spec"]["config"] == {"budget_usd": 20}
        assert both["plan"]["budget_usd"] == 20 and "20 долларов" in both["plan"]["summary"]
        only = review(brief(config={"budget_usd": 7.5}))
        assert only["spec"]["config"] == {"budget_usd": 7.5} and only["plan"]["budget_usd"] == 7.5

    def test_a_budget_that_is_not_agreed_in_the_config_is_not_agreed(self):
        answer = review(brief(budget_usd=10, config={"budget_usd": None}))
        assert answer["ok"] is True
        assert answer["spec"]["config"] == {"budget_usd": None}
        assert answer["plan"]["budget_usd"] is None
        assert "бюджет не согласован" in answer["plan"]["summary"]


# ---------------------------------------------------------------- the engine's own check


class TestTheEngineOwnCheck:
    def test_it_is_the_function_that_checks_a_whole_specification(self):
        assert onboarding.validate_team_spec is validation.validate_team_spec
        assert onboarding.normalize_path is validation.normalize_path
        assert onboarding.find_cycle is validation.find_cycle
        assert onboarding.find_write_overlaps is validation.find_write_overlaps
        assert onboarding.TEXT_LIMITS is validation.TEXT_LIMITS

    def test_what_the_engine_reports_and_nothing_here_did_is_a_problem_in_words(self):
        answer = review(brief(config={"heartbeat_timeout": 60}))
        assert answer["ok"] is False and answer["spec"] is None
        assert answer["questions"] == []          # an engine message is never a question
        [problem] = answer["problems"]
        assert problem["stage"] is None
        assert problem["what"] == ("Дополнительные настройки написаны с ошибкой: "
                                   "руководитель команды должен их поправить.")
        assert problem["detail"] == "config has unknown keys: heartbeat_timeout"

    def test_it_is_shown_beside_what_was_found_here(self):
        answer = review(brief(config={"nope": 1},
                              stages=[stage("Сбор", 1, specialist="enginer")]))
        assert [problem["stage"] for problem in answer["problems"]] == ["Сбор", None]

    def test_the_engine_never_repeats_what_was_already_said(self):
        for wrong, given, _about, _needle in WRONG:
            answer = review(given)
            assert not any("detail" in problem for problem in answer["problems"]), wrong

    def test_an_issue_about_a_stage_nobody_spoke_of_names_it(self, monkeypatch):
        issue = validation.Issue("E_TASK_TITLE", "tasks[1].title is missing or empty")
        monkeypatch.setattr(onboarding, "validate_team_spec",
                            lambda *_args, **_kwargs: validation.ValidationResult(errors=(issue,)))
        answer = review(brief(stages=[stage("Сбор", 1), stage("Отчёт", 2)]))
        [problem] = answer["problems"]
        assert problem == {"stage": "Отчёт",
                           "what": "Система не приняла этап «Отчёт»: руководитель команды "
                                   "должен посмотреть подробности и поправить его.",
                           "detail": "tasks[1].title is missing or empty"}
        assert answer["ok"] is False and answer["questions"] == []

    @pytest.mark.parametrize("named", [("stage-1", "stage-2"), ("stage-2", "stage-1")])
    def test_an_issue_about_two_stages_is_about_the_one_nobody_spoke_of(self, monkeypatch, named):
        # Whichever order the engine names the two stages in.
        issue = validation.Issue("E_WRITE_OVERLAP", "two tasks write the same file", task_ids=named)
        monkeypatch.setattr(onboarding, "validate_team_spec",
                            lambda *_args, **_kwargs: validation.ValidationResult(errors=(issue,)))
        answer = review(brief(stages=[stage("Сбор", 1, specialist="enginer"), stage("Отчёт", 2)]))
        problems = {problem["stage"]: problem["what"] for problem in answer["problems"]}
        # The problem about «Сбор» is the one found here; the engine's words
        # are about the stage nothing was said of.
        assert set(problems) == {"Сбор", "Отчёт"}
        assert problems["Сбор"].startswith("В этапе «Сбор» выбран специалист")
        assert problems["Отчёт"].startswith("Система не приняла этап «Отчёт»")

    def test_an_issue_about_the_whole_plan_does_not_name_a_stage(self, monkeypatch):
        issue = validation.Issue("E_AGENTS_INVALID", "agents[0] must be an object")
        monkeypatch.setattr(onboarding, "validate_team_spec",
                            lambda *_args, **_kwargs: validation.ValidationResult(errors=(issue,)))
        answer = review(brief(stages=[stage("Сбор", 1)]))
        [problem] = answer["problems"]
        assert problem["stage"] is None and problem["what"].startswith("Система не приняла план")
        assert problem["detail"] == "agents[0] must be an object"

    def test_a_crash_inside_the_engine_check_is_never_an_answer_lost(self, monkeypatch):
        def broken(*_args, **_kwargs):
            raise RuntimeError("boom")
        monkeypatch.setattr(onboarding, "validate_team_spec", broken)
        answer = review(brief())
        [problem] = answer["problems"]
        assert problem == {"stage": None,
                           "what": "Система не смогла проверить план: руководитель команды "
                                   "должен посмотреть подробности.",
                           "detail": "RuntimeError: boom"}
        assert answer["ok"] is False and answer["spec"] is None and answer["questions"] == []
        assert list(answer) == ANSWER_KEYS and isinstance(answer["plan"], dict)

    def test_nothing_here_looks_at_the_disk(self, monkeypatch, tmp_path):
        looks = []
        real = onboarding.normalize_path

        def spy(*args, **kwargs):
            looks.append(kwargs)
            return real(*args, **kwargs)

        def probing(_root):
            raise AssertionError("the engine's check was asked to look at the disk")

        monkeypatch.setattr(onboarding, "normalize_path", spy)
        monkeypatch.setattr(validation, "filesystem_is_case_insensitive", probing)
        monkeypatch.chdir(tmp_path)
        before = sorted(tmp_path.iterdir())
        answers = [review(FULL),
                   review(brief(stages=[stage("Сбор", 1, writes=["../x.md", "/etc/passwd"])])),
                   review({})]
        # A look at the disk would be caught by review and come back as a
        # problem: there is none beyond the two files outside the project.
        assert answers[0]["ok"] is True and answers[2]["problems"] == []
        assert len(answers[1]["problems"]) == 2 and all(problem["stage"] == "Сбор"
                                                        for problem in answers[1]["problems"])
        assert sorted(tmp_path.iterdir()) == before
        assert looks and all(call == {"check_fs": False} for call in looks), looks

    def test_a_real_crash_of_the_engine_check_is_caught_too(self):
        # Keys of two kinds in one config: the engine sorts them and fails.
        answer = review(brief(config={1: "a", "b": 2}))
        [problem] = answer["problems"]
        assert answer["ok"] is False and answer["questions"] == []
        assert problem["stage"] is None and problem["detail"].startswith("TypeError:")
        assert "Система не смогла проверить план" in problem["what"]


# ---------------------------------------------------------------- the module


class TestTheModule:
    FIELDS = ["title", "request", "result", "constraints", "materials", "deadline",
              "budget_usd", "quality", "stages", "config"]
    STAGE_FIELDS = ["title", "description", "specialist", "writes", "done_when", "after", "check"]

    @pytest.mark.parametrize("field", FIELDS + STAGE_FIELDS, ids=lambda field: f"``{field}``")
    def test_the_docstring_documents_the_brief_field_by_field(self, field):
        doc = " ".join(onboarding.__doc__.split())
        assert f"``{field}``" in doc, field

    def test_the_docstring_tells_what_is_critical_and_how_to_use_it(self):
        doc = " ".join(onboarding.__doc__.split())       # as one reads it, line wraps gone
        for words in ("review(brief)", "team.py init", "BriefError", "up to 120 signs",
                      "up to 1000 signs", "up to 500 signs", "at least one", "AI-команда",
                      "Ожидаемый результат: "):
            assert words in doc, words
        for key, (name, _lens) in SPECIALISTS.items():
            assert f"``{key}`` ({name})" in doc or f"``{key}`` (AI-специалист)" in doc, key

    def test_it_uses_the_standard_library_and_the_package_only(self):
        tree = ast.parse(Path(onboarding.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:          # absolute: it may only be the standard library
                    imported.add(node.module.split(".")[0])
                else:                        # relative: the package itself
                    assert node.level >= 1, ast.dump(node)
        assert imported <= set(sys.stdlib_module_names), imported
        assert "__future__" in imported

    def test_the_syntax_is_that_of_python_3_10(self):
        source = Path(onboarding.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 10))
        future = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                  and node.module == "__future__"]
        assert future and [alias.name for alias in future[0].names] == ["annotations"]

    def test_the_one_entry_point(self):
        assert onboarding.__all__ == ["BriefError", "SPECIALISTS", "review"]
        assert list(inspect.signature(review).parameters) == ["brief"]
