"""The eight scenarios of the specification, end to end (v2.1 spec 22.4).

Each one runs on a real git repository through the engine's own
operations and is judged twice: by what the engine did, and by what the
owner sees on the board afterwards (the read-only snapshot the page is
built from). The scenarios are:

A. normal work: five specialists, ten stages, all in parallel;
B. a failed check: the result goes back and is accepted the second time;
C. recovery: a specialist stopped answering, another continued the work;
D. a clash: the results cannot be merged until it is resolved;
E. the budget: the limit is reached and new stages do not start;
F. watching: the board shows what really happens without a restart;
G. the size: the page holds a hundred stages and twenty specialists;
H. a person who is not technical: every question has its answer on the
   board, and no term of the engine has to be explained.
"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_teams import dashboard_data, engine
from agent_teams.dashboard_server import serve
from agent_teams.errors import BudgetExceeded
from agent_teams.storage import open_store
from big_team import build_big_team

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "unit" / "page_harness.js"
DASH = ROOT / "plugins/agent-teams/skills/agent-teams/scripts/agent_teams/dashboard"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def project(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
    # The team's directory and its specification are not part of the project.
    (tmp_path / ".gitignore").write_text(".agent-teams/\nspec.json\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@e.i")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def stage(number: int, assignee: str, **fields) -> dict:
    base = {"id": f"s{number}", "title": f"Этап {number}", "assignee": assignee,
            "lens": "behavior", "description": f"Что нужно сделать на этапе {number}",
            "files_write": [f"out/s{number}.md"], "acceptance": [f"этап {number} готов"], "deps": []}
    base.update(fields)
    return base


def start(root: Path, tasks: list[dict], config: dict | None = None) -> str:
    """Create the team and return the lead token."""
    spec = {"title": "Запуск продукта", "goal": "Подготовить обзор рынка и план",
            "config": config or {}, "tasks": tasks}
    (root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return engine.op_init(root, "t", root / "spec.json")["lead_token"]


def deliver(root: Path, claim: dict, by: str, text: str, name: str | None = None) -> None:
    """The executor writes its file in its own workspace and hands it in.

    The file is the task's own (``out/<task>.md``) unless ``name`` says
    another: a stage may be told to write elsewhere.
    """
    task_id = claim["task"]
    workspace = Path(claim["workspace"])
    target = workspace / (name or f"out/{task_id}.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", task_id)
    engine.op_submit(root, "t", task_id, by, claim["execution_token"])


def accept_by_decision(root: Path, lead: str, task_id: str) -> None:
    engine.op_verify(root, "t", task_id, "pass", "", lead, override_reason="demo")


def board(root: Path) -> dict:
    return dashboard_data.snapshot(root, "t")


def root_path(project: Path) -> Path:
    return project


def stages_of(snap: dict) -> dict:
    return {t["title"]: t for t in snap["tasks"]}


def get(port: int, path: str) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    assert response.status == 200, data
    return json.loads(data)


# ------------------------------------------------------------------ A

def test_a_normal_work_five_specialists_ten_stages_in_parallel(project):
    lead = start(project, [stage(n, f"w{n % 5}") for n in range(1, 11)])
    # Every specialist takes both of its stages before anybody hands in.
    claims = {n: engine.op_claim(project, "t", f"s{n}", f"w{n % 5}") for n in range(1, 11)}
    assert all(c["owner"] == f"w{n % 5}" for n, c in claims.items())
    for n in range(1, 11):
        deliver(project, claims[n], f"w{n % 5}", f"часть {n}\n")
        accept_by_decision(project, lead, f"s{n}")
    engine.op_integrate(project, "t", lead, apply=True)

    status = engine.op_status(project, "t")
    assert [t["status"] for t in status["tasks"]] == ["completed"] * 10
    assert status["run"]["status"] == "finished"
    assert all((project / "out" / f"s{n}.md").read_text(encoding="utf-8") == f"часть {n}\n"
               for n in range(1, 11))

    snap = board(project)
    assert snap["progress"] == {"done": 10, "active": 0, "needs_attention": 0, "total": 10,
                                "percent": 100}
    assert snap["finished"] is True and snap["run"]["state"] == "Завершено"
    assert snap["integration"]["state"] == "applied"
    assert all(step["done"] for step in snap["integration"]["steps"])
    quality = snap["quality"]
    assert (quality["accepted_by_decision"], quality["judged"], quality["handed_over"]) == (10, 0, 10)
    # Five specialists, each with both of its stages done; and the checker,
    # who had nothing to check: every result was a decision of the lead.
    executors = [a for a in snap["agents"] if a["name"] != "AI-проверяющий"]
    assert len(executors) == 5 and all(a["state"] == "Завершил" for a in executors)
    assert all(a["completed"] == 2 and a["total"] == 2 for a in executors)
    checker = next(a for a in snap["agents"] if a["name"] == "AI-проверяющий")
    assert checker["checks"] == 0 and checker["completed"] == 0
    assert quality["story"] == ("Результаты не проверялись. 10 результатов приняты решением "
                                "руководителя команды, без проверки.")
    mine = stages_of(snap)
    assert all(mine[f"Этап {n}"]["result"] == {
        "files": [f"out/s{n}.md"], "ready": True, "handed_over": True, "handed_over_count": 1}
        for n in range(1, 11))


# ------------------------------------------------------------------ B

def test_b_a_failed_check_goes_back_and_is_accepted_the_second_time(project):
    lead = start(project, [stage(1, "w1")])
    claim = engine.op_claim(project, "t", "s1", "w1")
    deliver(project, claim, "w1", "риски без оценок\n")
    engine.op_verify(project, "t", "s1", "fail", "нет оценок вероятности", lead)
    assert engine.op_status(project, "t")["tasks"][0]["status"] == "in_progress"
    deliver(project, claim, "w1", "риски с оценками\n")
    accept_by_decision(project, lead, "s1")

    snap = board(project)
    quality = snap["quality"]
    assert (quality["checks"], quality["failed"], quality["reworked"]) == (1, 1, 1)
    assert quality["problems"][0]["task"] == "Этап 1"
    assert quality["problems"][0]["remark"] == "нет оценок вероятности"
    stage_view = stages_of(snap)["Этап 1"]
    assert stage_view["state"] == "Принято" and stage_view["result"]["handed_over_count"] == 2
    assert [c["passed"] for c in stage_view["checks"]] == [False, True]
    # The problem is told with how it ended: it was dealt with, nobody waits.
    [incident] = snap["recovery"]["incidents"]
    assert incident["title"] == "Результат не прошёл проверку"
    assert incident["outcome"] == "исполнитель доработал результат и передал его снова"
    assert incident["human_needed"] is False and snap["recovery"]["open"] == 0
    # The failed check is a fact even though a decision accepted the result
    # in the end; the bar puts the result where it got: with the decisions.
    assert quality["split"] == {"first": 0, "reworked": 0, "decision": 1, "awaiting": 0}
    assert stage_view["state_hint"] == "принято решением руководителя команды, без проверки"


# ------------------------------------------------------------------ C

def test_c_a_specialist_who_stopped_answering_is_replaced(project):
    lead = start(project, [stage(1, "w1")])
    first = engine.op_claim(project, "t", "s1", "w1")
    # The executor goes silent: its last sign of life is an hour old.
    with open_store(project / ".agent-teams" / "t") as store, store.transaction():
        store.update_task("t", "s1", heartbeat_at=ago(hours=1))
    recovered = engine.op_recover(project, "t", lead)["recovered"]
    assert [(r["task"], r["action"]) for r in recovered] == [("s1", "requeued")]
    second = engine.op_claim(project, "t", "s1", "w2")
    assert second["owner"] == "w2" and second["attempt"] == 2
    deliver(project, second, "w2", "готово\n")
    accept_by_decision(project, lead, "s1")

    snap = board(project)
    [incident] = snap["recovery"]["incidents"]
    assert incident["title"] == "Исполнитель перестал отвечать"
    assert incident["outcome"] == "задачу продолжил AI-специалист"   # a role, never an identifier
    assert incident["human_needed"] is False
    # One loss is one line in the feed too: the archive of the old
    # workspace, which the engine writes as a second timeout, is a note.
    feed = dashboard_data.snapshot(root_path(project), "t", event_limit=100)["events"]
    assert [e["title"] for e in feed].count("Специалист перестал отвечать") == 1
    assert not [e for e in feed if "архив" in e["title"]]
    assert stages_of(snap)["Этап 1"]["state"] == "Принято"


# ------------------------------------------------------------------ D

def test_d_results_that_cannot_be_merged_stop_at_the_clash(project):
    lead = start(project, [stage(1, "w1", files_write=["out/shared.md"]),
                           stage(2, "w2", files_write=["out/shared.md"])],
                 config={"allow_write_overlaps": True,
                         "allow_write_overlaps_reason": "a clash on purpose"})
    for number, by, text in ((1, "w1", "версия A\n"), (2, "w2", "версия B\n")):
        claim = engine.op_claim(project, "t", f"s{number}", by)
        deliver(project, claim, by, text, "out/shared.md")
        accept_by_decision(project, lead, f"s{number}")
    result = engine.op_integrate(project, "t", lead)
    assert result["ok"] is False and result["conflicts"]["files"] == ["out/shared.md"]
    assert engine.op_status(project, "t")["run"]["status"] == "integration_failed"
    assert not (project / "out" / "shared.md").exists()    # the project is untouched

    snap = board(project)
    assert snap["integration"]["state"] == "conflict"
    story = snap["integration"]["story"]
    assert "Не удалось объединить" in story and "Исходные версии сохранены" in story
    # Nothing pretends the clash is resolving itself: the owner is told.
    assert "пытается устранить" not in story and "запустить объединение заново" in story
    assert snap["run"]["state"] == "Нужно ваше внимание"
    assert [a["key"] for a in snap["actions"] if a["task"] is None] == ["integrate"]
    assert stages_of(snap)["Этап 1"]["state"] == "Принято"    # what was accepted still is


# ------------------------------------------------------------------ E

def test_e_the_budget_stops_new_work_and_the_board_says_why(project):
    lead = start(project, [stage(1, "w1"), stage(2, "w2")],
                 config={"budget_usd": 0.001, "budget_per_task_usd": 0.01})
    running = engine.op_claim(project, "t", "s1", "w1")
    engine.op_tokens(project, "t", "s1", 4000, "w1", "glm-5.3")     # more than the budget
    with pytest.raises(BudgetExceeded, match="BUDGET_LIMIT"):
        engine.op_claim(project, "t", "s2", "w2")

    snap = board(project)
    assert snap["budget"]["state"] == "limit"
    assert "Бюджет израсходован" in snap["budget"]["story"]
    assert "budget" in {a["kind"] for a in snap["attention"]}
    assert [a["key"] for a in snap["actions"] if a["task"] is None] == ["pause", "budget"]
    budget_problem = next(i for i in snap["recovery"]["incidents"] if i["kind"] == "budget")
    assert budget_problem["human_needed"] is True and budget_problem["action"].startswith("Увеличьте бюджет")
    assert stages_of(snap)["Этап 2"]["state"] == "В очереди"

    # The attempt that was already running is not stopped.
    deliver(project, running, "w1", "отчёт\n")
    accept_by_decision(project, lead, "s1")
    # The owner raises the budget from the board, and new work starts again.
    engine.op_action(project, "t", "budget", lead, usd=1.0, confirm=True)
    engine.op_claim(project, "t", "s2", "w2")
    assert board(project)["budget"]["state"] == "ok"


# ------------------------------------------------------------------ F

def test_f_the_board_shows_what_really_happens_without_a_restart(project):
    lead = start(project, [stage(1, "w1"), stage(2, "w2")])
    server = serve(project, "t", port=0)
    assert server is not None
    port = server.server_address[1]
    try:
        before = get(port, "/api/snapshot?team=t")
        assert {t["state"] for t in before["tasks"]} == {"В очереди"}
        assert get(port, "/api/status?team=t")["last_seq"] == before["last_seq"]

        claim = engine.op_claim(project, "t", "s1", "w1")
        status = get(port, "/api/status?team=t")
        assert status["last_seq"] > before["last_seq"]       # the poll sees the change
        after = get(port, "/api/snapshot?team=t")
        assert {t["title"]: t["state"] for t in after["tasks"]} == {
            "Этап 1": "В работе", "Этап 2": "В очереди"}
        assert after["events"][-1]["title"] == "Приступил к задаче"

        deliver(project, claim, "w1", "без оценок\n")
        engine.op_verify(project, "t", "s1", "fail", "нет оценок", lead)
        last = get(port, "/api/snapshot?team=t")
        remark = stages_of(last)["Этап 1"]
        assert remark["failed_checks"] == 1 and remark["checks"][-1]["feedback"] == "нет оценок"
        assert last["quality"]["problems"][0]["remark"] == "нет оценок"
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ G

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_g_the_page_holds_a_hundred_stages_and_twenty_specialists(tmp_path):
    info = build_big_team(tmp_path)
    snap = dashboard_data.snapshot(info["root"], "big")
    proc = subprocess.run(["node", str(HARNESS), str(DASH), "render"],
                          input=json.dumps([snap]), capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    out = json.loads(proc.stdout)
    assert "crashed" not in out and out["console"] == [], out.get("console")
    dom = out["dom"]
    assert dom["graph"]["html"].count('class="node ') == 100
    assert dom["planList"]["html"].count('class="stage ') == 100
    assert dom["timeline"]["html"].count('class="trow"') == 100
    assert dom["teamList"]["html"].count('class="person ') == 20
    # And what is not drawn is said: the marks a long history leaves out.
    assert dom["timeline"]["html"].count("ещё ") == 100


# ------------------------------------------------------------------ H

def test_h_every_question_of_a_newcomer_has_its_answer_on_the_board(project):
    lead = start(project, [stage(1, "w1"), stage(2, "w2", deps=["s1"]), stage(3, "w1"),
                           stage(4, "w3")],
                 config={"budget_usd": 5.0})
    # 1 is accepted by decision; 2 is in work; 3 failed two checks and waits;
    # 4 waits for its turn.
    first = engine.op_claim(project, "t", "s1", "w1")
    deliver(project, first, "w1", "данные о рынке\n")
    accept_by_decision(project, lead, "s1")
    engine.op_claim(project, "t", "s2", "w2")
    third = engine.op_claim(project, "t", "s3", "w1")
    for text in ("риски без оценок\n", "риски, всё ещё без оценок\n"):
        deliver(project, third, "w1", text)
        engine.op_verify(project, "t", "s3", "fail", "нет оценок вероятности", lead)
    engine.op_tokens(project, "t", "s1", 20000, "w1", "glm-5.3")

    snap = board(project)
    mine = stages_of(snap)

    # 1. Is the work finished?
    assert snap["finished"] is False and snap["run"]["state"] == "В работе"
    assert snap["progress"]["done"] < snap["progress"]["total"]
    # 2. What is the team doing now?
    working = [t for t in snap["tasks"] if t["state"] == "В работе"]
    assert [t["title"] for t in working] == ["Этап 2"] and working[0]["next_step"]
    assert next(a for a in snap["agents"] if "Этап 2" in [t["title"] for t in a["tasks"]])["doing"]
    # 3. Find a specialist: three of one role are told apart by a number.
    executors = sorted(a["name"] for a in snap["agents"] if a["name"] != "AI-проверяющий")
    assert executors == ["AI-аналитик 1", "AI-аналитик 2", "AI-аналитик 3"]
    assert all(a["does"] and a["state"] for a in snap["agents"])
    # 4. The progress of one stage.
    assert mine["Этап 2"]["checkpoints"] == {"started": True, "submitted": False, "accepted": False}
    assert mine["Этап 1"]["checkpoints"]["accepted"] and mine["Этап 1"]["worked_s"] is not None
    # 5. Find a problem.
    assert [a["title"] for a in snap["attention"]] and snap["recovery"]["incidents"]
    assert mine["Этап 3"]["state"] == "Нужно ваше внимание"
    # 6. Is my intervention needed?
    waiting = next(a for a in snap["attention"] if a["task"] == "s3")
    assert waiting["action"] and waiting["why"] and waiting["what"]
    assert any(i["human_needed"] for i in snap["recovery"]["incidents"])
    # Both places ask about the same stage: a stop after two failed checks
    # is a problem that waits, not only a line on the attention card.
    assert "Сейчас нужна ваша помощь по 1 задаче" in snap["recovery"]["story"]
    assert any(i["kind"] == "blocked" and i["human_needed"] for i in snap["recovery"]["incidents"])
    # 7. See the finished result.
    assert mine["Этап 1"]["result"]["ready"] and mine["Этап 1"]["result"]["files"]
    # 8. The amount spent.
    assert snap["budget"]["spent_text"].startswith("$") and snap["budget"]["by_task"]
    # 9. Why the work is delayed: the stage says what it waits for, and
    #    what a stage needs of another is said by its name.
    assert "решени" in mine["Этап 3"]["next_step"], mine["Этап 3"]["next_step"]
    assert mine["Этап 2"]["deps"] == ["Этап 1"]
    # 10. The history of what was done.
    assert len(snap["events"]) >= 8 and all(e["title"] and e["ts"] for e in snap["events"])
    assert {e["specialist"] for e in snap["events"]} - {"Руководитель команды", "Система", "AI-проверяющий"} \
        <= {a["name"] for a in snap["agents"]}

    # And all of it in the words of a person: no specialist is named by
    # its identifier, and no model name reaches the simple level.
    said = json.dumps(snap, ensure_ascii=False)
    for identifier in ("w1", "w2", "w3", "glm-5.3"):
        assert identifier not in said, identifier


# ------------------------------------------------------------------ the page with everything at once

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_first_picture_of_a_live_team_takes_no_technical_word_to_the_page(project):
    lead = start(project, [stage(1, "w1"), stage(2, "w2")])
    claim = engine.op_claim(project, "t", "s1", "w1")
    deliver(project, claim, "w1", "отчёт\n")
    engine.op_verify(project, "t", "s1", "fail", "нет оценок", lead)
    snap = board(project)
    proc = subprocess.run(["node", str(HARNESS), str(DASH), "render"],
                          input=json.dumps([snap]), capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    out = json.loads(proc.stdout)
    assert "crashed" not in out and out["console"] == []
    # What a person reads is the text between the tags: no specialist is
    # named by an identifier. (The name of a result's file is the file's
    # own: the stage's id in it is the file the stage was told to write.)
    text = re.sub(r"<[^>]+>", " ", "\n".join(part["html"] for part in out["dom"].values()))
    for identifier in ("w1", "w2"):
        assert not re.search(rf"\b{identifier}\b", text), identifier
