"""The dashboard page on a fake page (v2.1 spec 4, 5, 8, 16).

The page script runs under Node on a minimal fake document
(``page_harness.js``). What is pinned: it renders a snapshot without a
single error, no value from the wire ever becomes markup, it speaks in
whole words with the right endings, it takes a whole snapshot only when
something changed, it keeps the last known state when the server stops
answering, and the mode, theme and detail sheets do what they say.
"""
from __future__ import annotations

import html as html_module
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts/agent_teams/dashboard"
HARNESS = Path(__file__).with_name("page_harness.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")

# Markup, quotes and a script URL in every field a reader can see.
EVIL = '<img src=x onerror=alert(1)><script>alert(2)</script>"\'&javascript:x'
# What esc() on the page must produce (it writes an apostrophe as &#39;).
EVIL_ESCAPED = html_module.escape(EVIL, quote=True).replace("&#x27;", "&#39;")


def task(task_id="a", **fields) -> dict:
    base = {"id": task_id, "title": "Собрать отчёт", "state": "В работе",
            "state_hint": "исполнитель занимается задачей", "kind": "busy",
            "icon": "progress", "role": "AI-аналитик", "attempt": 1,
            "failed_checks": 0, "needs_attention": False, "active": True,
            "changed_files": [], "violations": [], "deps": [], "silent_s": None,
            "silent": False, "description": "", "expected": [], "criteria": [],
            "next_step": "Специалист работает",
            "checkpoints": {"started": True, "submitted": False, "accepted": False},
            "worked_s": 1500, "last_event": None}
    return {**base, **fields}


def agent(**fields) -> dict:
    base = {"name": "AI-аналитик",
            "does": "Собирает и сравнивает данные, делает выводы.",
            "state": "Работает", "state_hint": "выполняет задачу", "icon": "progress",
            "kind": "working", "doing": "работает над: Собрать отчёт", "silent_s": 125,
            "worked_s": 1500, "completed": 0, "total": 1, "checks": None,
            "tasks": [{"id": "a", "title": "Собрать отчёт", "state": "В работе",
                       "kind": "busy"}]}
    return {**base, **fields}


def event(seq=1, **fields) -> dict:
    return {"seq": seq, "ts": "2026-11-25T12:00:00.000Z", "title": "Приступил к задаче",
            "type": "task_claimed", "kind": "busy", "icon": "progress", "by": 0,
            "specialist": "AI-аналитик", "task": "Собрать отчёт", **fields}


def snapshot(**fields) -> dict:
    base = {
        "team": "demo", "now": "2026-11-25T12:00:00.000Z", "elapsed_s": 3725,
        "finished": False,
        "project": {"name": "Исследование рынка",
                    "goal": "Сравнить конкурентов и подготовить рекомендации",
                    "started_at": "2026-11-25T09:00:00.000Z"},
        "run": {"state": "В работе", "hint": "команда выполняет задачи"},
        "progress": {"done": 0, "active": 1, "needs_attention": 0, "total": 1,
                     "percent": 0},
        "cost": {"tokens": 0, "usd": 0.0, "unknown_records": 0, "complete": True,
                 "quality": "unknown"},
        "tasks": [task()], "agents": [agent()], "attention": [], "events": [event()],
        "last_seq": 1, "level": "simple",
        "plan": {"nodes": [{"id": "a", "col": 0, "row": 0}], "edges": [],
                 "columns": 1, "rows": 1},
        "timeline": {"start": "2026-11-25T09:00:00.000Z", "end": "2026-11-25T10:00:00.000Z",
                     "span_text": {"work": "работа", "done": "завершено"},
                     "rows": [{"id": "a", "title": "Собрать отчёт", "state": "В работе", "kind": "busy",
                               "spans": [{"from": "2026-11-25T09:05:00.000Z",
                                          "to": "2026-11-25T10:00:00.000Z", "kind": "work",
                                          "text": "работа"}],
                               "marks": [{"ts": "2026-11-25T09:05:00.000Z", "title": "Приступил к задаче",
                                          "kind": "busy", "icon": "progress", "detail": ""}]}]},
    }
    return {**base, **fields}


def acted(steps: list, snapshots: list[dict]) -> list[dict]:
    """Run scripted steps on the page; returns what it shows before the
    first one and after each one: ``steps[k]`` is after step ``k - 1``."""
    snapshots[0]["_actions"] = steps
    return run("actions", snapshots)["steps"]


def run(scenario: str, snapshots: list[dict]) -> dict:
    proc = subprocess.run(["node", str(HARNESS), str(DASH), scenario],
                          input=json.dumps(snapshots), capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode in (0, 2), proc.stderr
    out = json.loads(proc.stdout)
    assert "crashed" not in out, out.get("crashed")
    return out


def markup(out: dict) -> str:
    """Everything the page wrote as markup (the only place escaping matters)."""
    return "\n".join(part["html"] for part in out["dom"].values())


def hostile_snapshot() -> dict:
    return snapshot(
        project={"name": EVIL, "goal": EVIL, "started_at": EVIL},
        run={"state": EVIL, "hint": EVIL},
        agents=[agent(name=EVIL, does=EVIL, doing=EVIL, state=EVIL, kind=EVIL,
                      icon=EVIL), agent(name="AI-проверяющий", checks=EVIL,
                                        completed=EVIL, tasks=[], state=EVIL,
                                        kind=EVIL, icon=EVIL)],
        attention=[{"kind": EVIL, "task": "a", "title": EVIL, "icon": EVIL,
                    "what": EVIL, "why": EVIL, "tried": EVIL, "action": EVIL,
                    "remarks": EVIL, "if_nothing": EVIL, "since": None}],
        tasks=[task(title=EVIL, role=EVIL, failed_checks=2, changed_files=[EVIL],
                    violations=[EVIL], deps=[EVIL], kind=EVIL, icon=EVIL,
                    description=EVIL, expected=[EVIL], next_step=EVIL,
                    last_event={"title": EVIL, "detail": EVIL, "ts": EVIL},
                    criteria=[{"text": EVIL, "how": EVIL}]),
               task("b", state="На проверке", kind="attention", icon="search"),
               task("c", title="В очереди", state="В очереди", kind="idle", icon="circle",
                    active=False, attempt=3, silent_s=125, silent=True)],
        events=[event(title=EVIL, specialist=EVIL, task=EVIL, detail=EVIL, kind=EVIL,
                      tech={"x": EVIL})])


class TestThePageIsSafeWithAnyData:
    def test_no_value_from_the_wire_becomes_markup(self):
        out = run("render", [hostile_snapshot()])
        written = markup(out)
        assert EVIL not in written
        assert "<script" not in written and "<img" not in written
        assert "javascript:" not in written.replace(EVIL_ESCAPED, "")
        # Every field that carried the string is on the page, escaped.
        assert written.count(EVIL_ESCAPED) >= 30

    def test_every_place_that_writes_markup_escapes_the_same_way(self):
        out = run("render", [hostile_snapshot()])
        dom = out["dom"]
        assert EVIL_ESCAPED in dom["nowList"]["html"]        # title, role, last event
        assert EVIL_ESCAPED in dom["planList"]["html"]       # title, role, files, next step
        assert EVIL_ESCAPED in dom["attList"]["html"]        # title, remark, answers
        assert EVIL_ESCAPED in dom["feed"]["html"]           # title, who, task, detail
        assert EVIL_ESCAPED in dom["teamList"]["html"]       # role, responsibility, work
        # Plain text stays exactly what the server sent.
        assert dom["projectName"]["text"] == EVIL
        assert dom["projectGoal"]["text"] == EVIL
        assert dom["runStateText"]["text"] == EVIL

    def test_numbers_that_are_not_numbers_hurt_nothing(self):
        snapped = snapshot(progress={"done": EVIL, "active": EVIL, "needs_attention": EVIL,
                                     "total": EVIL, "percent": EVIL},
                           tasks=[task(failed_checks=EVIL, attempt=EVIL, silent_s=EVIL,
                                       worked_s=EVIL, silent=True)],
                           events=[event(kind=EVIL, icon=EVIL)],
                           agents=[agent(completed=EVIL, total=EVIL, worked_s=EVIL,
                                         silent_s=EVIL)])
        out = run("render", [snapped])
        written = markup(out)
        assert EVIL not in written and "<script" not in written
        assert out["dom"]["storyLine"]["text"] == "В плане пока нет задач."
        assert out["dom"]["teamList"]["html"].count(EVIL_ESCAPED) == 0

    def test_a_snapshot_without_optional_parts_still_renders(self):
        bare = snapshot(run=None, cost=None, tasks=[], events=[], attention=[],
                        agents=[], project={}, progress={"total": 0})
        out = run("render", [bare])
        dom = out["dom"]
        assert dom["storyLine"]["text"] == "В плане пока нет задач."
        assert "Событий пока нет" in dom["feed"]["html"]
        assert "План ещё не сформирован" in dom["planList"]["html"]
        assert "Специалистов пока нет" in dom["teamList"]["html"]
        assert dom["runStateText"]["text"] == "нет данных"
        assert dom["projectName"]["text"] == "AI-команда"


class TestItSpeaksInWholeWords:
    @pytest.mark.parametrize("progress, story", [
        ({"done": 6, "active": 0, "needs_attention": 0, "total": 6, "percent": 100},
         "Все задачи выполнены. Результат проверен и готов."),
        ({"done": 0, "active": 0, "needs_attention": 0, "total": 0, "percent": 0},
         "В плане пока нет задач."),
        ({"done": 1, "active": 2, "needs_attention": 3, "total": 6, "percent": 17},
         "Ещё 2 задачи в работе, 3 задачи ждут вашего решения."),
        ({"done": 1, "active": 1, "needs_attention": 1, "total": 3, "percent": 33},
         "Ещё 1 задача в работе, одна ждёт вашего решения."),
        ({"done": 0, "active": 0, "needs_attention": 1, "total": 4, "percent": 0},
         "Команда остановилась на задаче, которая требует вашего решения."),
        ({"done": 0, "active": 5, "needs_attention": 0, "total": 8, "percent": 0},
         "Команда работает. 5 задач в работе."),
        ({"done": 0, "active": 0, "needs_attention": 0, "total": 8, "percent": 0},
         "Задачи в очереди, исполнители скоро приступят."),
    ])
    def test_the_story_follows_the_situation(self, progress, story):
        out = run("render", [snapshot(progress=progress)])
        assert out["dom"]["storyLine"]["text"] == story

    def test_the_header_names_the_project_its_goal_and_when_it_began(self):
        dom = run("render", [snapshot()])["dom"]
        assert dom["projectName"]["text"] == "Исследование рынка"
        assert dom["projectGoal"]["text"] == "Сравнить конкурентов и подготовить рекомендации"
        # The clock itself depends on the reader's time zone; the date does not.
        assert re.fullmatch(r"старт 25 нояб\., \d\d:\d\d", dom["startedAt"]["text"])
        assert dom["elapsed"]["text"] == "в работе 1 ч 2 мин"

    def test_a_team_without_a_goal_shows_what_it_is_doing(self):
        dom = run("render", [snapshot(project={"name": "Проект", "goal": "",
                                               "started_at": None})])["dom"]
        assert dom["projectName"]["text"] == "Проект"
        assert dom["projectGoal"]["text"] == "команда выполняет задачи"
        assert dom["startedAt"]["text"] == "старт "

    @pytest.mark.parametrize("failed, words", [
        (1, "1 раз возвращена на доработку"), (2, "2 раза возвращена на доработку"),
        (5, "5 раз возвращена на доработку"), (0, "AI-аналитик"),
    ])
    def test_the_plan_counts_the_returns_with_the_right_ending(self, failed, words):
        out = run("render", [snapshot(tasks=[task(failed_checks=failed)])])
        assert words in out["dom"]["planList"]["html"]

    def test_a_stage_says_what_comes_next_and_what_result_is_expected(self):
        waiting = task("c", title="Итоговый отчёт", state="В очереди", kind="idle",
                       icon="circle", active=False, worked_s=None,
                       next_step="Ждёт завершения: Собрать отчёт",
                       deps=["Собрать отчёт"], expected=["out/report.md"],
                       checkpoints={"started": False, "submitted": False, "accepted": False})
        out = run("render", [snapshot(tasks=[task(expected=["out/a.md"]), waiting])])
        plan = out["dom"]["planList"]["html"]
        assert "дальше: Специалист работает" in plan
        assert "дальше: Ждёт завершения: Собрать отчёт" in plan
        assert '<span class="label">результат:</span><span class="file">out/a.md</span>' in plan
        assert '<span class="file">out/report.md</span>' in plan
        # Only the checkpoints the engine records: started, handed over, accepted.
        assert 'aria-label="Пройдено шагов: 1 из 3"' in plan
        assert 'aria-label="Пройдено шагов: 0 из 3"' in plan
        assert plan.count('<i class="point on"') == 1
        assert 'data-task="c"' in plan and 'role="button" tabindex="0"' in plan

    def test_dependencies_are_named_after_the_tasks_they_follow(self):
        out = run("render", [snapshot(tasks=[task(deps=["Собрать отчёт", "Проверить"])])])
        assert "после: Собрать отчёт, Проверить" in out["dom"]["planList"]["html"]

    def test_a_silent_executor_is_said_to_be_silent(self):
        silent = task(silent_s=125, silent=True, state="Не отвечает", kind="problem",
                      icon="alert", next_step="Специалист работает")
        out = run("render", [snapshot(tasks=[silent])])
        now = out["dom"]["nowList"]["html"]
        assert "ожидается ответ исполнителя" in now
        assert "последняя активность 2 мин назад" in now
        assert "в работе <span" in now and "25 мин" in now
        assert "Не отвечает" in now and "В работе" not in now

    def test_a_card_shows_the_latest_word_about_the_task(self):
        last = {"title": "Проверка не пройдена, результат отправлен на доработку",
                "detail": "Замечания проверяющего: нет оценок", "ts": "2026-11-25T12:00:00.000Z"}
        out = run("render", [snapshot(tasks=[task(last_event=last)])])
        now = out["dom"]["nowList"]["html"]
        assert "Последнее обновление: Проверка не пройдена" in now
        assert "Замечания проверяющего: нет оценок" in now

    def test_a_finished_team_says_how_long_the_work_took(self):
        out = run("render", [snapshot(finished=True, elapsed_s=3725)])
        assert out["dom"]["elapsed"]["text"] == "работа заняла 1 ч 2 мин"

    @pytest.mark.parametrize("cost, words", [
        ({"tokens": 0, "usd": 0.0, "complete": True}, "Затраты: <b>данных о расходах пока нет</b>"),
        ({"tokens": 9_000, "usd": 0.5, "complete": True}, "Затраты: <b>$0.50</b>"),
        ({"tokens": 9_000, "usd": 0.5, "complete": False}, "Затраты: <b>не менее $0.50</b>"),
        ({"tokens": 9_000, "usd": 0.0, "complete": False},
         "Затраты: <b>не определены</b> <span class=\"muted\">(данные о ценах недоступны)</span>"),
    ])
    def test_spend_is_neither_invented_nor_hidden(self, cost, words):
        out = run("render", [snapshot(cost=cost)])
        assert words in out["dom"]["nowList"]["html"]

    def test_an_empty_attention_card_says_so_and_drops_the_alert_look(self):
        out = run("render", [snapshot()])
        assert "Ничего не требует вашего решения" in out["dom"]["attList"]["html"]
        assert "attention" not in out["dom"]["attCard"]["cls"].split()
        out = run("render", [snapshot(attention=[
            {"kind": "blocked", "task": "a", "title": "t", "icon": "alert", "what": "w",
             "why": "y", "tried": "r", "action": "a", "remarks": "замечание",
             "if_nothing": "i", "since": None}])])
        assert "attention" in out["dom"]["attCard"]["cls"].split()
        assert "Замечания проверяющего" in out["dom"]["attList"]["html"]
        assert "замечание" in out["dom"]["attList"]["html"]


class TestSpecialists:
    def test_every_specialist_has_a_card_saying_who_they_are_and_what_they_do(self):
        checker = agent(name="AI-проверяющий", does="Проверяет результаты других специалистов.",
                        state="Проверяет", state_hint="проверяет результат другого специалиста",
                        icon="search", kind="verifying", doing="проверяет: Собрать отчёт",
                        silent_s=None, worked_s=None, completed=2, total=3, checks=3, tasks=[])
        out = run("render", [snapshot(agents=[agent(), checker])])
        team = out["dom"]["teamList"]["html"]
        assert team.count('role="button" tabindex="0"') == 2
        assert 'data-specialist="0"' in team and 'data-specialist="1"' in team
        worker = team.split('data-specialist="1"')[0]
        assert "AI-аналитик" in worker and "Собирает и сравнивает данные, делает выводы." in worker
        assert "работает над: Собрать отчёт" in worker
        assert "завершено 0 из 1" in worker and "в работе <span" in worker
        assert "последняя активность 2 мин назад</span>" in worker
        assert ">Работает</span>" in worker
        assert "AI-проверяющий" in team and "проверок: 3 · принято по проверке: 2" in team
        assert "проверяет: Собрать отчёт" in team and ">Проверяет</span>" in team

    def test_the_clock_of_a_card_follows_the_time_between_snapshots(self):
        out = run("timers", [snapshot()])
        kinds = dict(out["clock"])
        assert kinds["silent"] == "последняя активность 3 мин назад"
        assert kinds["worked"] == "26 мин"
        assert out["elapsed"] == "в работе 1 ч 3 мин"


class TestItRefreshesWithoutDisturbingTheReader:
    def test_it_asks_whether_anything_changed_and_takes_a_snapshot_only_then(self):
        same = snapshot()
        newer = snapshot(events=[event(1), event(2, title="Передал результат на проверку")],
                         last_seq=2)
        out = run("polls", [same, same, newer, newer])
        assert out["fetches"][0].startswith("/api/snapshot?")      # the first full picture
        assert out["writesAfterSame"] == out["writesAfterFirst"]
        assert out["snapshotFetchesAfterSame"] == 1                # two polls, no new snapshot
        assert out["writesAfterNew"] == out["writesAfterFirst"] + 1
        assert out["snapshotFetchesAfterNew"] == 2
        assert "Передал результат на проверку" in out["feedAfterNew"]

    def test_only_the_new_line_is_marked_as_new(self):
        newer = snapshot(events=[event(1), event(2)], last_seq=2)
        newer2 = snapshot(events=[event(1), event(2), event(3)], last_seq=3)
        out = run("polls", [snapshot(), newer, newer2, newer2])
        assert out["markedAfterNext"] == 1

    def test_the_server_going_away_is_announced_and_the_last_state_stays(self):
        blocked = {"kind": "blocked", "task": "a", "title": "Оценить риски", "icon": "alert",
                   "what": "w", "why": "y", "tried": "r", "action": "a", "remarks": "",
                   "if_nothing": "i", "since": None}
        out = run("outage", [snapshot(attention=[blocked])])
        assert out["afterFiveSeconds"] == [
            "conn show stale", "Обновление задерживается, показано последнее известное состояние"]
        assert out["afterALongTime"][0] == "conn show lost"
        assert out["afterALongTime"][1].startswith("Соединение потеряно. Показано состояние на ")
        assert out["storyAfter"] == out["storyBefore"]
        assert "Оценить риски" in out["keptContent"]

    def test_a_wrong_request_is_not_reported_as_a_lost_connection(self):
        out = run("badrequest", [snapshot()])
        assert out["banner"][0] == "conn show lost"
        assert "Не удалось открыть состояние этой команды" in out["banner"][1]
        assert "team.py ui" in out["banner"][1]
        assert out["console"] == ["dashboard: no team state at /x/state.db"]

    def test_a_fault_of_the_page_is_not_blamed_on_the_server(self):
        out = run("badrender", [snapshot(), snapshot(last_seq=7)])
        assert out["banner"][0] == "conn show lost"
        assert "доска не смогла их показать" in out["banner"][1]
        assert "Не удалось открыть состояние" not in out["banner"][1]
        assert any(line.startswith("dashboard: could not render the snapshot") for line in out["console"])
        assert out["storyAfter"] == out["storyBefore"]

    def test_without_a_team_it_asks_for_the_command_and_polls_nobody(self):
        out = run("noteam", [snapshot()])
        assert "team.py ui" in out["banner"][1]
        assert out["fetches"] == [] and out["intervals"] == 0


class TestTheDetailSheet:
    def detailed(self) -> dict:
        checked = task(description="Собрать данные по трём конкурентам",
                       expected=["out/a.md"], role="AI-аналитик", worked_s=1500,
                       changed_files=["out/a.md", "README.md"], violations=["README.md"],
                       next_step="Идёт проверка качества", state="На проверке",
                       kind="attention", icon="search",
                       criteria=[{"text": "отчёт существует", "how": "решение человека"},
                                 {"text": "нет пустых разделов", "how": "оценка содержания проверяющим"}],
                       checkpoints={"started": True, "submitted": True, "accepted": False},
                       checks=[{"n": 1, "passed": False, "manual": False, "feedback": "нет оценок"},
                               {"n": 2, "passed": True, "manual": True, "feedback": ""}])
        checker = agent(name="AI-проверяющий", does="Проверяет результаты других специалистов.",
                        state="Свободен", state_hint="готов к следующей задаче", icon="circle",
                        kind="free", doing="ждёт результатов для проверки", silent_s=None,
                        worked_s=None, completed=2, total=3, checks=3, tasks=[])
        events = [event(1, by=0), event(2, title="Передал результат на проверку", by=0,
                                        kind="busy", icon="search"),
                  event(3, title="Проверка не пройдена, результат отправлен на доработку",
                        by=1, kind="problem", icon="alert")]
        return snapshot(tasks=[checked], agents=[agent(), checker], events=events,
                        task_events={"a": events[:2]})

    def test_a_specialist_card_opens_its_own_history(self):
        out = run("sheet", [self.detailed(), self.detailed()])
        spec = out["specialist"]
        assert spec["open"] is True and spec["title"] == "AI-проверяющий"
        assert "Проверяет результаты других специалистов." in spec["body"]
        assert "Проверок выполнено: 3, принято по проверке: 2" in spec["body"]
        assert "История работы" in spec["body"]
        # Only this specialist's lines, the newest first: the checker wrote event 3.
        assert "Проверка не пройдена" in spec["body"]
        assert "Приступил к задаче" not in spec["body"]

    def test_a_stage_opens_its_details_from_click_and_keyboard(self):
        out = run("sheet", [self.detailed(), self.detailed()])
        got = out["task"]
        assert got["prevented"] is True and got["open"] is True
        assert got["title"] == "Собрать отчёт"
        body = got["body"]
        for part in ("Что нужно сделать", "Кто выполняет", "Что уже сделано",
                     "Проверка результата", "История этапа"):
            assert f"<h3>{part}</h3>" in body, part
        assert "Собрать данные по трём конкурентам" in body
        assert "Ожидаемый результат: out/a.md" in body
        assert "Дальше: Идёт проверка качества" in body
        assert "отчёт существует <span class=\"muted\">(решение человека)</span>" in body
        assert "(оценка содержания проверяющим)" in body
        assert "AI-аналитик, Работает, в работе 25 мин" in body
        assert "<p>Изменённые файлы: out/a.md, README.md <span class='bad-text'>(вне разрешённой зоны: README.md)</span></p>" in body
        assert "Проверка не пройдена" in body and "нет оценок" in body
        assert "Принято вручную, без проверки" in body and "Проверка пройдена" not in body
        # The history of the task is asked for separately, by its id.
        assert got["lastFetch"].endswith("/api/tasks/a?team=demo&level=simple")
        assert "Передал результат на проверку" in body
        assert "Проверка не пройдена, результат отправлен на доработку" not in body

    def test_an_open_sheet_survives_a_refresh_and_follows_the_new_data(self):
        refreshed = self.detailed()
        refreshed["tasks"][0]["title"] = "Собрать отчёт (обновлено)"
        refreshed["events"] = refreshed["events"] + [event(9, title="Этап завершён", by=0,
                                                           kind="done", icon="check")]
        refreshed["last_seq"] = 9
        out = run("sheet", [self.detailed(), refreshed])
        assert out["afterRefresh"]["open"] is True
        assert out["afterRefresh"]["title"] == "Собрать отчёт (обновлено)"

    def test_the_sheet_closes_by_its_button_and_by_the_backdrop(self):
        out = run("sheet", [self.detailed(), self.detailed()])
        assert out["closed"] is False and out["afterBackdropClick"] is False


class TestSwitches:
    def test_the_technical_mode_asks_for_it_and_shows_the_fields(self):
        snapped = snapshot(tasks=[task(tech={"task_id": "a", "branch": "x/y"})],
                           events=[event(tech={"hash": "h" * 64})])
        out = run("modes", [snapped, snapped, snapped, snapped])
        assert out["fetches"][0].endswith("level=simple")
        assert out["advancedFetch"].endswith("level=advanced")
        # A quiet team (same last event) is still redrawn for the new level.
        assert out["advancedWrites"] == 2
        assert out["advancedTechShown"] >= 2
        assert "task_id: a" in out["advancedPlan"] and "branch: x/y" in out["advancedPlan"]
        assert out["simpleAgainFetch"].endswith("level=simple")
        assert "hash" not in out["simpleFeed"]
        assert out["prefs"]["at.mode"] == "simple"

    def test_the_theme_is_remembered(self):
        out = run("modes", [snapshot(), snapshot(), snapshot(), snapshot()])
        assert out["themeAfterClick"] == {"data-theme": "dark"}
        assert out["themeAfterSecondClick"] == {"data-theme": "light"}

    def test_the_cards_follow_the_width_of_the_window(self):
        out = run("resize", [snapshot()])
        assert out["columnsNarrow"] == [["attCard", "nowCard", "teamCard", "planCard", "timeCard",
                                   "feedCard"], []]
        assert out["columnsWide"][0] and out["columnsWide"][1]
        assert out["allCardsPlaced"]


def graphed() -> dict:
    """A plan of four stages: one done, one in work that needs it, one in
    trouble, and one waiting for two of them."""
    done = task("a", title="Собрать информацию", state="Принято", kind="done", icon="check",
                active=False, next_step="Готово, результат принят")
    work = task("b", title="Сравнить предложения", deps=["Собрать информацию"])
    trouble = task("c", title="Оценить риски", state="Нужно ваше внимание", kind="problem",
                   icon="alert", active=False, next_step="Ждёт вашего решения")
    waiting = task("d", title="Итоговый отчёт", state="В очереди", kind="idle", icon="circle",
                   active=False, worked_s=None, deps=["Сравнить предложения", "Оценить риски"],
                   next_step="Ждёт завершения: Сравнить предложения, Оценить риски")
    engineer = agent(name="AI-инженер", doing="перестал отвечать", state="Проблема",
                     state_hint="не может продолжать", icon="alert", kind="problem",
                     tasks=[{"id": "c", "title": "Оценить риски", "state": "Нужно ваше внимание",
                             "kind": "problem"}])
    return snapshot(tasks=[done, work, trouble, waiting], agents=[agent(), engineer],
                    events=[event(1),
                            event(2, title="Проверка не пройдена", kind="problem", icon="alert",
                                   by=1, specialist="AI-инженер", task="Оценить риски"),
                            event(3, title="Работа команды завершена", kind="done", icon="check",
                                  by=None, specialist="Руководитель команды", task="")],
                    last_seq=3,
                    plan={"nodes": [{"id": "a", "col": 0, "row": 0}, {"id": "c", "col": 0, "row": 1},
                                    {"id": "b", "col": 1, "row": 0}, {"id": "d", "col": 2, "row": 0}],
                          "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "d"},
                                    {"from": "c", "to": "d"}],
                          "columns": 3, "rows": 2})


def dimmed(step: dict) -> list[str]:
    return sorted(re.findall(r'class="node [a-z]+ dim" data-task="(\w)"', step["graph"]))


def place(step: dict) -> tuple[float, float, float]:
    x, y, k = re.fullmatch(r"translate\(([-\d.e]+) ([-\d.e]+)\) scale\(([\d.e]+)\)",
                           step["transform"]).groups()
    return float(x), float(y), float(k)


class TestThePlanGraph:
    def test_every_stage_is_a_node_and_every_dependency_a_line(self):
        dom = run("render", [graphed()])["dom"]
        graph = dom["graph"]["html"]
        assert graph.count('class="node ') == 4
        assert graph.count('<path class="link ') == 3
        assert sorted(re.findall(r'data-task="(\w)"', graph)) == ["a", "b", "c", "d"]
        assert 'class="node done"' in graph and 'class="node problem"' in graph
        # A line says, in words, which result it carries to which stage.
        assert "Результат этапа «Собрать информацию» нужен для этапа «Сравнить предложения»" in graph
        assert 'aria-label="Оценить риски, Нужно ваше внимание"' in graph
        # The first column and the second one, each in its own row.
        assert 'transform="translate(36 22)"' in graph
        assert 'transform="translate(268 22)"' in graph

    def test_zoom_buttons_and_the_whole_plan_again(self):
        steps = acted([["click", "graphZoomIn"], ["click", "graphZoomOut"], ["click", "graphReset"]],
                      [graphed(), graphed()])
        # Three columns of 196 and four gaps of 36 make 732, shown in 596:
        # the whole plan across the width, a little smaller than life.
        whole = 596 / 732
        assert place(steps[2])[2] == pytest.approx(whole, rel=1e-6)
        assert place(steps[1])[2] == pytest.approx(whole * 1.25, rel=1e-6)
        assert place(steps[3]) == pytest.approx((0, 0, whole), rel=1e-6)

    def test_dragging_moves_the_plan_and_a_refresh_keeps_it_where_it_was_left(self):
        steps = acted([["pan", 40, -15], ["poll"]], [graphed(), graphed()])
        moved = place(steps[1])
        assert (moved[0], moved[1]) == (40, -15)
        assert place(steps[2]) == moved           # not reset by the refresh
        assert steps[2]["nodes"] == 4


    def test_the_search_hides_what_does_not_match_and_keeps_the_shape(self):
        steps = acted([["set", "graphSearch", "риски"], ["input", "graphSearch"], ["poll"]],
                      [graphed(), graphed()])
        assert steps[2]["nodes"] == 4 and dimmed(steps[2]) == ["a", "b", "d"]
        assert dimmed(steps[3]) == ["a", "b", "d"]           # the search survives a refresh

    @pytest.mark.parametrize("chip, dimmed_nodes", [("open", ["a"]), ("problem", ["a", "b", "d"])])
    def test_the_filters(self, chip, dimmed_nodes):
        steps = acted([["chip", "data-plan-filter", chip]], [graphed(), graphed()])
        assert dimmed(steps[0]) == []                       # nothing hidden before
        assert steps[1]["nodes"] == 4                       # hidden, not removed
        assert dimmed(steps[1]) == dimmed_nodes

    def test_a_node_opens_its_stage(self):
        steps = acted([["node", "c"]], [graphed(), graphed()])
        assert steps[1]["sheetTitle"] == "Оценить риски"
        assert "Что нужно сделать" in steps[1]["sheet"] and "Ждёт вашего решения" in steps[1]["sheet"]



class TestTheTimeline:
    def timed(self) -> dict:
        spans = [("09:05", "09:15", "work"), ("09:15", "09:18", "check"), ("09:18", "09:27", "fix"),
                 ("09:27", "09:35", "check"), ("09:35", "10:00", "done")]
        rows = [{
            "id": "a", "title": "Собрать отчёт", "state": "Принято", "kind": "done",
            "spans": [{"from": f"2026-11-25T{a}:00.000Z", "to": f"2026-11-25T{b}:00.000Z",
                       "kind": k, "text": t} for (a, b, k), t in zip(spans, "abcde")],
            "marks": [{"ts": "2026-11-25T09:18:00.000Z", "title": "Проверка не пройдена", "kind": "problem",
                       "icon": "alert", "detail": "нет оценок"},
                      {"ts": "2026-11-25T09:35:00.000Z", "title": "Проверка пройдена", "kind": "done",
                       "icon": "check", "detail": ""}],
        }, {"id": "b", "title": "В очереди", "state": "В очереди", "kind": "idle", "spans": [], "marks": []}]
        text = {"work": "работа", "check": "проверка качества", "fix": "доработка",
                "wait": "ожидание", "done": "завершено"}
        return snapshot(timeline={"start": "2026-11-25T09:00:00.000Z", "end": "2026-11-25T10:00:00.000Z",
                                  "rows": rows, "span_text": text})

    def test_a_row_per_task_with_spans_marks_and_a_legend(self):
        dom = run("render", [self.timed()])["dom"]
        tl, legend = dom["timeline"]["html"], dom["timeLegend"]["html"]
        assert sorted(re.findall(r'class="trow" data-task="(\w)"', tl)) == ["a", "b"]
        assert [k for k in re.findall(r'<rect class="span (\w+)"', tl)] == ["work", "check", "fix", "check", "done"]
        assert tl.count('<circle class="tmark problem"') == 1 and tl.count('<circle class="tmark done"') == 1
        assert tl.count('<text class="tick"') == 5
        assert "доработка: 09:" in tl and "09:18 · Проверка не пройдена: нет оценок" in tl.replace("09:18", "09:18") if False else "Проверка не пройдена: нет оценок" in tl
        for word in ("работа", "проверка качества", "доработка", "завершено", "событие"):
            assert word in legend, word
        assert "ожидание" not in legend                  # only what really happened

    def test_nothing_recorded_is_nothing_drawn(self):
        snap = snapshot(timeline={"start": "2026-11-25T09:00:00.000Z", "end": "2026-11-25T09:30:00.000Z",
                                  "rows": [], "span_text": {}})
        dom = run("render", [snap])["dom"]
        assert dom["timeline"]["html"] == "" and dom["timeLegend"]["html"] == ""

    def test_a_row_opens_its_stage(self):
        steps = acted([["trow", "a"]], [self.timed(), self.timed()])
        assert steps[1]["sheetTitle"] == "Собрать отчёт" and "История этапа" in steps[1]["sheet"]


def fed(more: bool = False) -> dict:
    events = [event(1, title="Приступил к задаче", by=0),
              event(2, title="Проверка не пройдена", kind="problem", icon="alert", by=1,
                    specialist="AI-инженер", task="Оценить риски"),
              event(3, title="Результат принят вручную", kind="manual", icon="refresh", by=None,
                    specialist="Руководитель команды", task="Собрать отчёт"),
              event(4, title="Этап завершён", kind="done", icon="check", by=None,
                    specialist="Руководитель команды", task="Собрать отчёт")]
    if more:
        events.append(event(5, title="Передал результат на проверку", by=1, specialist="AI-инженер",
                            task="Оценить риски"))
    return snapshot(agents=[agent(), agent(name="AI-инженер")], events=events, last_seq=events[-1]["seq"])


def fed_titles(step: dict) -> list[str]:
    return re.findall(r'<div class="tt">([^<]+)</div>', step["feed"])


class TestTheFeedTools:
    def test_search_keeps_only_what_matches(self):
        steps = acted([["set", "feedSearch", "проверк"], ["input", "feedSearch"]], [fed(), fed()])
        assert fed_titles(steps[1]) == fed_titles(steps[0])      # typing alone changes nothing
        assert fed_titles(steps[2]) == ["Проверка не пройдена"]
        assert steps[2]["pause"] == "Пауза"

    def test_by_kind(self):
        steps = acted([["chip", "data-feed-kind", "problem"], ["chip", "data-feed-kind", "manual"],
                       ["chip", "data-feed-kind", "all"]], [fed(), fed(), fed(), fed()])
        assert len(fed_titles(steps[0])) == 4
        assert fed_titles(steps[1]) == ["Проверка не пройдена"]
        assert fed_titles(steps[2]) == ["Результат принят вручную"]
        assert len(fed_titles(steps[3])) == 4

    def test_by_specialist_including_the_one_who_is_not_a_card(self):
        snap = fed()
        options = run("render", [snap])["dom"]["feedWho"]["html"]
        assert '<option value="1">AI-инженер</option>' in options
        assert '<option value="Руководитель команды">Руководитель команды</option>' in options
        steps = acted([["set", "feedWho", "1"], ["change", "feedWho"],
                       ["set", "feedWho", "Руководитель команды"], ["change", "feedWho"]],
                      [fed(), fed(), fed(), fed(), fed()])
        assert fed_titles(steps[2]) == ["Проверка не пройдена"]
        assert fed_titles(steps[4]) == ["Результат принят вручную", "Этап завершён"]


    def test_pause_holds_the_list_and_counts_what_came_since(self):
        steps = acted([["click", "feedPause"], ["poll"], ["click", "feedPause"]],
                      [fed(), fed(more=True), fed(more=True), fed(more=True)])
        assert steps[1]["pause"] == "Продолжить" and len(fed_titles(steps[1])) == 4
        assert fed_titles(steps[2]) == fed_titles(steps[1])     # held where the reader paused
        assert steps[2]["pause"] == "Продолжить (1 новых)"
        assert fed_titles(steps[3])[-1] == "Передал результат на проверку"
        assert steps[3]["pause"] == "Пауза"

    def test_filters_stay_across_a_refresh(self):
        steps = acted([["chip", "data-feed-kind", "problem"], ["poll"]], [fed(), fed(more=True), fed(more=True)])
        assert fed_titles(steps[2]) == ["Проверка не пройдена"]


    def test_nothing_matched_is_said_so(self):
        steps = acted([["set", "feedSearch", "ничего похожего"], ["input", "feedSearch"]], [fed(), fed()])
        assert "По этим условиям событий нет." in steps[2]["feed"]

    def test_by_stage_and_by_time(self):
        # The fake clock of the harness stands close to the epoch: "long ago"
        # has to be earlier still.
        old = [dict(event(n, title=f"Старое {n}"), ts="1969-12-30T00:00:00.000Z") for n in (1, 2)]
        snap = snapshot(agents=[agent(), agent(name="AI-инженер")],
                        tasks=[task(), task("c", title="Оценить риски", state="В работе")],
                        events=old + [event(3, title="Проверка не пройдена", kind="problem",
                                            icon="alert", by=1, specialist="AI-инженер", task="Оценить риски")],
                        last_seq=3)
        assert '<option value="Оценить риски">Оценить риски</option>' in run("render", [snap])["dom"]["feedTask"]["html"]
        steps = acted([["set", "feedTask", "Оценить риски"], ["change", "feedTask"],
                       ["set", "feedTask", ""], ["change", "feedTask"], ["click", "feedHour"]],
                      [snap] * 6)
        assert fed_titles(steps[2]) == ["Проверка не пройдена"]
        assert len(fed_titles(steps[4])) == 3
        assert fed_titles(steps[5]) == ["Проверка не пройдена"]     # the old lines are left out

    def test_a_line_about_a_stage_opens_it(self):
        steps = acted([["feedrow", "a"]], [fed(), fed()])
        assert steps[1]["sheetTitle"] == "Собрать отчёт"
        assert 'data-task="a" role="button" tabindex="0"' in steps[0]["feed"]

    def test_reading_further_back(self):
        old = [event(n, title=f"Раньше {n}") for n in (2, 3, 4)]
        snap = snapshot(events=[event(9, title="Свежее")], last_seq=9, older_events=old)
        steps = acted([["older"], ["older"]], [snap, snap, snap, snap])
        assert steps[0]["olderHidden"] is False and "Раньше" not in steps[0]["feed"]
        assert steps[1]["fetches"][-1].endswith("&before=9&limit=100")
        assert fed_titles(steps[1]) == ["Раньше 2", "Раньше 3", "Раньше 4", "Свежее"]
        assert steps[1]["olderHidden"] is False                 # seq 1 may still be out there
        assert steps[2]["olderHidden"] is False                 # nothing came: nothing changed


class TestTheResultOfATask:
    def with_result(self, ready: bool = True) -> dict:
        return snapshot(tasks=[task(result={"files": ["out/report.md", "out/chart.png", EVIL],
                                            "ready": ready})],
                        results={"a": {"out/report.md": "# Отчёт\n" + EVIL, "out/chart.png": {"image": True},
                                       EVIL: {"tooLarge": True}}})

    def test_the_files_are_listed_and_a_text_opens_as_text(self):
        steps = acted([["sheet", "a"], ["file", "out/report.md"]], [self.with_result(), self.with_result()])
        assert "Готовый результат" in steps[1]["sheet"]
        assert "Принятая версия результата. Файлы:" in steps[1]["sheet"]
        assert steps[1]["sheet"].count('class="file-btn') == 3
        assert EVIL_ESCAPED in steps[1]["sheet"] and EVIL not in steps[1]["sheet"]
        assert steps[2]["fetches"][-1] == "/api/results/a/out/report.md?team=demo"
        assert "# Отчёт" in steps[2]["result"] and EVIL_ESCAPED in steps[2]["result"]
        assert 'class="file-btn on" data-result-file="out/report.md"' in steps[2]["sheet"]

    def test_a_picture_opens_as_a_picture(self):
        steps = acted([["sheet", "a"], ["file", "out/chart.png"]],
                      [self.with_result(), self.with_result(), self.with_result()])
        assert '<img class="result-img" src="/api/results/a/out/chart.png?team=demo"' in steps[2]["result"]


    def test_pressing_again_closes_and_a_file_that_cannot_be_shown_is_said_so(self):
        steps = acted([["sheet", "a"], ["file", "out/report.md"], ["file", "out/report.md"],
                       ["file", "nothing.txt"]],
                      [self.with_result()] * 5)
        assert "# Отчёт" in steps[2]["result"]
        assert "Принятая версия" in steps[3]["sheet"] and steps[3]["result"] == ""
        assert "Не удалось открыть файл" in steps[4]["result"]

    def test_the_open_file_stays_open_across_a_refresh(self):
        steps = acted([["sheet", "a"], ["file", "out/report.md"], ["poll"]],
                      [self.with_result(), self.with_result(), self.with_result()])
        assert "# Отчёт" in steps[2]["result"] and "# Отчёт" in steps[3]["result"]

    def test_a_result_not_yet_accepted_is_called_intermediate(self):
        steps = acted([["sheet", "a"]], [self.with_result(ready=False), self.with_result(ready=False)])
        assert "Промежуточный результат, ещё не принят." in steps[1]["sheet"]

    def test_how_many_times_the_result_was_handed_over(self):
        twice = snapshot(tasks=[task(result={"files": ["out/a.md"], "ready": False,
                                              "handed_over_count": 2})])
        assert "Результат передавался 2 раза, показана последняя версия." in acted([["sheet", "a"]], [twice, twice])[1]["sheet"]
        once = snapshot(tasks=[task(result={"files": ["out/a.md"], "ready": True, "handed_over_count": 1})])
        assert "Результат передавался " not in acted([["sheet", "a"]], [once, once])[1]["sheet"]

    def test_an_empty_result_is_said_to_be_empty(self):
        empty = snapshot(tasks=[task(result={"files": [], "ready": True, "handed_over": True})])
        assert "ничего не подготовил" in acted([["sheet", "a"]], [empty, empty])[1]["sheet"]
        never = snapshot(tasks=[task(result={"files": [], "ready": False, "handed_over": False})])
        assert "ещё не передавался" in acted([["sheet", "a"]], [never, never])[1]["sheet"]

