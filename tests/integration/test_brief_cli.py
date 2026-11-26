"""A task in plain words through the command line (v2.1 spec 14).

A person who is not technical does not write a specification: the lead
talks with the owner and fills a brief in human words. ``team.py plan
--brief`` answers it (what to ask, what is written wrongly, the plan to
confirm) and ``team.py init --brief`` creates the team from it, once
nothing is missing. What is pinned:

- the answer is a document, pure ASCII in every mode, and ``plan``
  exits with 1 while something is still to ask or to fix;
- only the four critical gaps are asked, one question at a time;
- the team the engine creates from a brief is the team the brief
  described: ids, specialists, files, criteria, the order of the stages;
- what the engine sees only on a real disk is a problem in words;
- a brief that is not ready creates nothing and says which question to
  ask the owner.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts"
TEAM = SCRIPTS / "team.py"

BRIEF = {
    "title": "Сравнение конкурентов",
    "request": "Изучи рынок конкурентов, сравни их предложения и подготовь стратегию запуска продукта",
    "result": "Отчёт со сравнением и стратегией запуска",
    "constraints": ["только открытые источники"],
    "materials": ["docs/product.md"],
    "budget_usd": 10,
    "quality": ["каждый вывод со ссылкой на источник"],
    "stages": [
        {"title": "Собрать данные о конкурентах", "description": "Пять конкурентов, их предложения и цены",
         "specialist": "analyst", "writes": ["reports/competitors.md"],
         "done_when": ["в таблице не меньше пяти конкурентов"]},
        {"title": "Подготовить стратегию запуска", "specialist": "writer",
         "writes": ["reports/strategy.md"], "check": "python -m doctest reports/strategy.md",
         "done_when": ["в стратегии есть сроки и первые шаги"],
         "after": ["Собрать данные о конкурентах"]},
    ],
}


@pytest.fixture
def project(tmp_path):
    (root := tmp_path / "project").mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "product.md").write_text("продукт\n", encoding="utf-8")
    return root


def brief(root: Path, **changes) -> Path:
    data = json.loads(json.dumps(BRIEF))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path = root.parent / "brief.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TEAM), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


def answer(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout)


def rows(root: Path, team: str = "t"):
    with sqlite3.connect(f"file:{root / '.agent-teams' / team / 'state.db'}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT id, title, assignee, lens, files_write_json, acceptance_json, deps_json"
            " FROM tasks ORDER BY rowid")]


class TestPlan:
    def test_a_ready_brief_is_a_document_and_exits_with_zero(self, project):
        proc = run("plan", "--brief", str(brief(project)), "--project-root", str(project), "--json")
        assert proc.returncode == 0, proc.stderr
        got = answer(proc)
        assert got["ok"] is True and got["question"] is None and got["problems"] == []
        assert [t["id"] for t in got["spec"]["tasks"]] == ["stage-1", "stage-2"]
        plan = got["plan"]
        assert plan["stage_count"] == 2 and plan["budget_usd"] == 10
        assert [s["title"] for s in plan["stages"]] == ["Собрать данные о конкурентах",
                                                        "Подготовить стратегию запуска"]
        assert plan["stages"][1]["after"] == ["Собрать данные о конкурентах"]
        assert [(s["name"], s["stages"]) for s in plan["specialists"]] == [("AI-аналитик", 1),
                                                                           ("AI-редактор документации", 1)]
        assert plan["summary"] == ("В работе 2 специалиста и 2 этапа, бюджет 10 долларов, "
                                   "ограничения: только открытые источники.")

    def test_the_answer_is_ascii_in_the_text_mode_too(self, project):
        proc = run("plan", "--brief", str(brief(project)), "--project-root", str(project))
        assert proc.returncode == 0, proc.stderr
        assert all(ord(char) < 128 for char in proc.stdout), "a non-ASCII sign in a pipe"
        assert json.loads(proc.stdout)["ok"] is True        # a document is printed whole

    @pytest.mark.parametrize("missing,question", [
        ("request", "Что вы просите команду сделать?"),
        ("writes", "Какие файлы должны получиться на этапе «Собрать данные о конкурентах»?"),
        ("done_when", "Как понять, что этап «Собрать данные о конкурентах» готов"),
    ])
    def test_a_critical_gap_is_one_question_and_exit_one(self, project, missing, question):
        path = brief(project)
        data = json.loads(path.read_text(encoding="utf-8"))
        if missing == "request":
            data.pop("request")
        else:
            data["stages"][0].pop(missing)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        proc = run("plan", "--brief", str(path), "--project-root", str(project), "--json")
        assert proc.returncode == 1
        got = answer(proc)
        assert got["ok"] is False and got["spec"] is None
        assert len(got["questions"]) == 1 and got["question"] == got["questions"][0]
        assert got["question"].startswith(question)

    def test_nothing_is_asked_about_what_may_be_absent(self, project):
        proc = run("plan", "--brief", str(brief(project, budget_usd=None, result=None,
                                                constraints=None, materials=None, quality=None)),
                   "--project-root", str(project), "--json")
        got = answer(proc)
        assert proc.returncode == 0 and got["questions"] == []
        assert got["plan"]["budget_usd"] is None
        assert "бюджет не согласован" in got["plan"]["summary"] and "ограничений нет" in got["plan"]["summary"]

    def test_what_is_written_wrongly_is_a_problem_in_words(self, project):
        proc = run("plan", "--brief", str(brief(project, budget_usd="десять")),
                   "--project-root", str(project), "--json")
        assert proc.returncode == 1
        got = answer(proc)
        assert got["question"] is None and len(got["problems"]) == 1
        assert got["problems"][0]["what"].startswith("Бюджет записан не как сумма")

    def test_what_the_engine_sees_only_on_a_real_disk_is_a_problem(self, project, tmp_path):
        # A link that leaves the project: invisible to the brief alone.
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("не для команды\n", encoding="utf-8")
        (project / "docs" / "elsewhere.md").symlink_to(outside / "secret.md")
        path = brief(project, stages=[{"title": "Прочитать чужое", "writes": ["docs/elsewhere.md"],
                                       "done_when": ["прочитано"]}])
        proc = run("plan", "--brief", str(path), "--project-root", str(project), "--json")
        assert proc.returncode == 1, proc.stdout
        got = answer(proc)
        assert got["ok"] is False and got["spec"] is None
        problem = next(p for p in got["problems"] if "папки проекта" in p["what"])
        assert problem["stage"] is None and problem["detail"]


class TestInit:
    def test_the_team_is_the_one_the_brief_described(self, project):
        proc = run("init", "--team", "t", "--brief", str(brief(project)), "--project-root", str(project), "--json")
        assert proc.returncode == 0, proc.stderr
        assert "lead_token" in answer(proc)
        first, second = rows(project)
        assert (first["id"], first["assignee"], first["lens"]) == ("stage-1", "spec-1", "behavior")
        assert (second["id"], second["assignee"], second["lens"]) == ("stage-2", "spec-2", "docs")
        assert json.loads(second["deps_json"]) == ["stage-1"]
        assert json.loads(first["files_write_json"]) == ["reports/competitors.md"]
        assert [c["type"] for c in json.loads(first["acceptance_json"])] == ["manual"]
        assert [c["type"] for c in json.loads(second["acceptance_json"])] == ["manual", "test"]
        assert json.loads(second["acceptance_json"])[1]["verification"] == "python -m doctest reports/strategy.md"
        assert "2 специалиста и 2 этапа" in answer(proc)["summary"]
        status = run("status", "--team", "t", "--project-root", str(project), "--json")
        assert [t["title"] for t in answer(status)["tasks"]] == ["Собрать данные о конкурентах",
                                                                 "Подготовить стратегию запуска"]

    def test_a_brief_that_is_not_ready_creates_nothing_and_says_the_question(self, project):
        path = brief(project, stages=[{"title": "Собрать данные", "done_when": ["готово"]}])
        proc = run("init", "--team", "t", "--brief", str(path), "--project-root", str(project), "--json")
        assert proc.returncode == 1
        assert "the brief is not ready" in proc.stderr and "Question for the owner" in proc.stderr
        # The question reaches the lead as it is, escaped for any pipe.
        assert "Какие файлы должны получиться" in json.dumps(json.loads(proc.stderr.splitlines()[1]),
                                                             ensure_ascii=False)
        assert not (project / ".agent-teams").exists()

    def test_the_specification_and_the_brief_cannot_be_given_together(self, project):
        proc = run("init", "--team", "t", "--brief", str(brief(project)), "--tasks", "x.json",
                   "--project-root", str(project))
        assert proc.returncode == 2 and "not allowed with" in proc.stderr

    def test_a_brief_that_is_not_json_is_refused_in_words(self, project):
        path = project.parent / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        proc = run("plan", "--brief", str(path), "--project-root", str(project), "--json")
        assert proc.returncode == 1 and "--brief is not valid JSON" in proc.stderr
