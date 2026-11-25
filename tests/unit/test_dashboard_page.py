"""The dashboard page on a fake page (v2.1 spec 16.4, 2.1).

The page script runs under Node on a minimal fake document
(``page_harness.js``). What is pinned: it renders a snapshot without a
single error, no value from the wire ever becomes markup, it speaks in
whole words with the right endings, it redraws only when the data
changed, it keeps the last known state when the server stops answering,
and the mode and theme switches do what they say.
"""
from __future__ import annotations

import html as html_module
import json
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
            "silent": False}
    return {**base, **fields}


def event(seq=1, **fields) -> dict:
    return {"seq": seq, "ts": "2026-11-25T12:00:00.000Z", "title": "Приступил к задаче",
            "type": "task_claimed", "kind": "busy", "icon": "progress",
            "specialist": "AI-аналитик", "task": "Собрать отчёт", **fields}


def snapshot(**fields) -> dict:
    base = {
        "team": "demo", "now": "2026-11-25T12:00:00.000Z", "elapsed_s": 3725,
        "finished": False,
        "run": {"state": "В работе", "hint": "команда выполняет задачи"},
        "progress": {"done": 0, "active": 1, "needs_attention": 0, "total": 1,
                     "percent": 0},
        "cost": {"tokens": 0, "usd": 0.0, "unknown_records": 0, "complete": True,
                 "quality": "unknown"},
        "tasks": [task()], "attention": [], "events": [event()], "last_seq": 1,
        "level": "simple",
    }
    return {**base, **fields}


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
        run={"state": EVIL, "hint": EVIL},
        attention=[{"kind": "blocked", "task": "a", "title": EVIL, "icon": "alert",
                    "what": EVIL, "why": EVIL, "tried": EVIL, "action": EVIL,
                    "remarks": EVIL, "if_nothing": EVIL, "since": None}],
        tasks=[task(title=EVIL, role=EVIL, failed_checks=2, changed_files=[EVIL],
                    violations=[EVIL], deps=[EVIL], kind=EVIL, icon=EVIL),
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
        assert written.count(EVIL_ESCAPED) >= 20

    def test_every_place_that_writes_markup_escapes_the_same_way(self):
        out = run("render", [hostile_snapshot()])
        dom = out["dom"]
        assert EVIL_ESCAPED in dom["nowList"]["html"]        # title, role, files
        assert EVIL_ESCAPED in dom["planList"]["html"]       # title, role, files, deps
        assert EVIL_ESCAPED in dom["attList"]["html"]        # title, remark, answers
        assert EVIL_ESCAPED in dom["feed"]["html"]           # title, who, task, detail
        # Plain text stays exactly what the server sent.
        assert dom["teamGoal"]["text"] == EVIL
        assert dom["runStateText"]["text"] == EVIL
        assert dom["teamName"]["text"] == "AI-команда · demo"

    def test_numbers_that_are_not_numbers_hurt_nothing(self):
        snapped = snapshot(progress={"done": EVIL, "active": EVIL, "needs_attention": EVIL,
                                     "total": EVIL, "percent": EVIL},
                           tasks=[task(failed_checks=EVIL, attempt=EVIL, silent_s=EVIL,
                                       silent=True)],
                           events=[event(kind=EVIL, icon=EVIL)])
        out = run("render", [snapped])
        written = markup(out)
        assert EVIL not in written and "<script" not in written
        assert out["dom"]["storyLine"]["text"] == "В плане пока нет задач."

    def test_a_snapshot_without_optional_parts_still_renders(self):
        bare = snapshot(run=None, cost=None, tasks=[], events=[], attention=[],
                        progress={"total": 0})
        out = run("render", [bare])
        assert out["dom"]["storyLine"]["text"] == "В плане пока нет задач."
        assert "Событий пока нет" in out["dom"]["feed"]["html"]
        assert "План ещё не сформирован" in out["dom"]["planList"]["html"]
        assert out["dom"]["runStateText"]["text"] == "нет данных"


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

    @pytest.mark.parametrize("failed, words", [
        (1, "1 раз возвращена на доработку"), (2, "2 раза возвращена на доработку"),
        (5, "5 раз возвращена на доработку"), (0, "AI-аналитик"),
    ])
    def test_the_plan_counts_the_returns_with_the_right_ending(self, failed, words):
        out = run("render", [snapshot(tasks=[task(failed_checks=failed)])])
        assert words in out["dom"]["planList"]["html"]

    def test_dependencies_are_named_after_the_tasks_they_follow(self):
        out = run("render", [snapshot(tasks=[task(deps=["Собрать отчёт", "Проверить"])])])
        assert "после: Собрать отчёт, Проверить" in out["dom"]["planList"]["html"]

    def test_a_silent_executor_is_said_to_be_silent(self):
        silent = task(silent_s=125, silent=True, state="Не отвечает", kind="problem",
                      icon="alert")
        out = run("render", [snapshot(tasks=[silent])])
        now = out["dom"]["nowList"]["html"]
        assert "ожидается ответ исполнителя" in now
        assert "последняя активность 2 мин назад" in now
        assert "Не отвечает" in now and "В работе" not in now

    def test_a_finished_team_says_how_long_the_work_took(self):
        out = run("render", [snapshot(finished=True, elapsed_s=3725)])
        assert out["dom"]["elapsed"]["text"] == "работа заняла 1 ч 2 мин"
        out = run("render", [snapshot(elapsed_s=59)])
        assert out["dom"]["elapsed"]["text"] == "в работе 59 с"

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


class TestItRefreshesWithoutDisturbingTheReader:
    def test_it_redraws_only_when_something_happened(self):
        same = snapshot()
        newer = snapshot(events=[event(1), event(2, title="Передал результат на проверку")],
                         last_seq=2)
        out = run("polls", [same, same, newer, newer])
        assert out["writesAfterSame"] == out["writesAfterFirst"]
        assert out["feedAfterSame"] == out["first"]
        assert out["writesAfterNew"] == out["writesAfterFirst"] + 1
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
        out = run("badrender", [snapshot()])
        assert out["banner"][0] == "conn show lost"
        assert "доска не смогла их показать" in out["banner"][1]
        assert "Не удалось открыть состояние" not in out["banner"][1]
        assert any(line.startswith("dashboard: could not render the snapshot") for line in out["console"])
        assert out["storyAfter"] == out["storyBefore"]

    def test_without_a_team_it_asks_for_the_command_and_polls_nobody(self):
        out = run("noteam", [snapshot()])
        assert "team.py ui" in out["banner"][1]
        assert out["fetches"] == [] and out["intervals"] == 0


class TestSwitches:
    def test_the_technical_mode_asks_for_it_and_shows_the_fields(self):
        snapped = snapshot(tasks=[task(tech={"task_id": "a", "branch": "x/y"})],
                           events=[event(tech={"hash": "h" * 64})])
        out = run("modes", [snapped, snapped, snapped, snapped])
        assert out["fetches"][0].endswith("level=simple")
        assert out["advancedFetch"].endswith("level=advanced")
        # A quiet team (same last event) is still redrawn for the new level.
        assert out["advancedWrites"] == 2
        assert out["advancedTechShown"] == 2
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
        assert out["columnsNarrow"] == [["attCard", "nowCard", "planCard", "feedCard"], []]
        assert out["columnsWide"][0] and out["columnsWide"][1]
        assert out["allCardsPlaced"]
