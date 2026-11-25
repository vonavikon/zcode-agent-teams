"""Read-only team state for the dashboard (v2.1 spec 16.2, 16.3).

A real team is driven through the engine in a git project, then read
the way the dashboard reads it. What is pinned here: the simple level
carries no raw identifier and no credential, the reader is told what
actually happened (a manual acceptance, a silent executor, the remark
of a checker), the read can never write, and it stays whole while the
engine is writing.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agent_teams import dashboard_data, engine
from agent_teams.dashboard_data import (DashboardDataError, _readonly, snapshot,
                                        team_dir)

AGENTS = {"w1", "w2", "verifier", "lead", "recovery"}
TITLES = {"a": "Собрать отчёт", "b": "Проверить отчёт", "c": "Оценить риски"}
# The engine's own format: UTC with a zone.
OLD_HEARTBEAT = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-2 hours')"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def spec_file(root: Path, config: dict | None = None) -> Path:
    spec = {"config": config or {}, "tasks": [
        {"id": "a", "title": TITLES["a"], "assignee": "w1", "lens": "docs",
         "files_write": ["out/a.md"], "acceptance": ["готово"], "deps": []},
        {"id": "b", "title": TITLES["b"], "assignee": "w2", "lens": "behavior",
         "files_write": ["out/b.md"], "acceptance": ["проверено"], "deps": ["a"]},
        {"id": "c", "title": TITLES["c"], "assignee": "w1", "lens": "robustness",
         "files_write": ["out/c.md"], "acceptance": ["риски"], "deps": []},
    ]}
    (root / "out").mkdir(exist_ok=True)
    path = root / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def init_git(root: Path) -> None:
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


def set_heartbeat(db: Path, task_id: str, sql_time: str) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE tasks SET heartbeat_at = " + sql_time + " WHERE id = ?",
                     (task_id,))


def _build_team(tmp_path):
    """One team with everything a reader can be told about.

    a: accepted by the lead's decision (two journal events, one decision)
    b: started, its executor went silent
    c: failed two checks and is blocked, the checker left a remark
    """
    init_git(tmp_path)
    lead = engine.op_init(tmp_path, "t", spec_file(tmp_path))["lead_token"]
    claims = {}

    def claim(task_id, by):
        claims[task_id] = engine.op_claim(tmp_path, "t", task_id, by)

    def deliver(task_id, by, text):
        # The executor works in its own worktree and commits there.
        workspace = Path(claims[task_id]["workspace"])
        (workspace / "out" / f"{task_id}.md").write_text(text, encoding="utf-8")
        git(workspace, "add", "-A")
        git(workspace, "commit", "-qm", "work")
        engine.op_submit(tmp_path, "t", task_id, by, claims[task_id]["execution_token"])

    claim("a", "w1")
    deliver("a", "w1", "отчёт\n")
    # A PASS without the verifier's report is the lead's own decision.
    engine.op_verify(tmp_path, "t", "a", "pass", "", lead, by="verifier",
                     override_reason="demo")

    claim("c", "w1")
    deliver("c", "w1", "риски без оценок\n")
    engine.op_verify(tmp_path, "t", "c", "fail", "нет оценок вероятности", lead)
    deliver("c", "w1", "риски, всё ещё без оценок\n")
    engine.op_verify(tmp_path, "t", "c", "fail", "по-прежнему нет оценок", lead)

    claim("b", "w2")
    db = team_dir(tmp_path, "t") / "state.db"
    set_heartbeat(db, "b", OLD_HEARTBEAT)
    return {"root": tmp_path, "lead": lead, "db": db,
            "tokens": {k: c["execution_token"] for k, c in claims.items()}}


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """Built once per module: git worktrees are slow and reading changes
    nothing. A test that writes asks for ``fresh_team``."""
    return _build_team(tmp_path_factory.mktemp("dashboard-team"))


@pytest.fixture
def fresh_team(tmp_path):
    return _build_team(tmp_path)


def strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"key:{key}"
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)
    elif isinstance(node, str):
        yield node


def by_id(snap: dict, task_id: str) -> dict:
    return next(t for t in snap["tasks"] if t["id"] == task_id)


def titles(snap: dict) -> list[str]:
    return [e["title"] for e in snap["events"]]


class TestSimpleLevelCarriesNoIdentifier:
    def test_no_agent_id_and_no_credential_key_leaves(self, team):
        snap = snapshot(team["root"], "t")
        found = {s for s in strings(snap) if s in AGENTS}
        assert found == set(), found
        keys = {s[4:] for s in strings(snap) if s.startswith("key:")}
        # "tokens" is the count of AI tokens; everything else that smells
        # like a credential must be absent.
        suspicious = [k for k in keys if re.search(r"token(?!s$)|hash|secret|password", k)]
        assert suspicious == [], suspicious

    def test_the_lead_and_execution_tokens_are_nowhere_in_either_level(self, team):
        secrets = [team["lead"], *team["tokens"].values()]
        for level in ("simple", "advanced"):
            dumped = json.dumps(snapshot(team["root"], "t", level=level), ensure_ascii=False)
            for secret in secrets:
                assert secret not in dumped

    def test_the_hash_of_the_lead_token_is_not_read(self, team):
        # The teams table holds it; the snapshot never selects that column.
        with sqlite3.connect(f"file:{team['db']}?mode=ro", uri=True) as conn:
            stored = conn.execute("SELECT lead_token_hash FROM teams").fetchone()[0]
        assert stored
        assert stored not in json.dumps(snapshot(team["root"], "t", level="advanced"))

    def test_tasks_dependencies_and_events_speak_in_names(self, team):
        snap = snapshot(team["root"], "t")
        assert by_id(snap, "b")["deps"] == [TITLES["a"]]
        assert by_id(snap, "b")["role"] == "AI-аналитик"
        for item in snap["events"]:
            assert item["task"] in (None, *TITLES.values())
            assert item["specialist"] not in AGENTS and item["specialist"]
        assert all("id" not in a for a in snap["agents"])


class TestTheReaderIsToldWhatHappened:
    def test_a_manual_acceptance_leaves_no_passed_check_behind(self, team):
        snap = snapshot(team["root"], "t")
        assert "Результат принят вручную, без отчёта о проверке" in titles(snap)
        assert "Проверка пройдена" not in titles(snap)
        [check] = by_id(snap, "a")["checks"]
        assert check == {"n": 1, "passed": True, "manual": True, "feedback": ""}

    def test_a_real_check_is_not_marked_manual(self, team):
        snap = snapshot(team["root"], "t")
        assert [c["manual"] for c in by_id(snap, "c")["checks"]] == [False, False]

    def test_the_remark_of_the_checker_reaches_the_attention_card_and_the_feed(self, team):
        snap = snapshot(team["root"], "t")
        [blocked] = [a for a in snap["attention"] if a["kind"] == "blocked"]
        assert blocked["task"] == "c"
        assert blocked["remarks"] == "по-прежнему нет оценок"
        assert "2 раза" in blocked["what"]
        # The first remark comes with the failed check; the one that
        # exhausted the checks comes with the stop, where the engine writes it.
        remarks = [e["detail"] for e in snap["events"]
                   if e["type"] in ("verification_failed", "task_blocked")]
        assert remarks == [
            "Замечания проверяющего: нет оценок вероятности",
            "Результат 2 раза не прошёл проверку."
            " Последнее замечание проверяющего: по-прежнему нет оценок"]

    def test_a_silent_executor_and_its_task_agree(self, team):
        # The engine's recovery reads the task's heartbeat; so does the board.
        snap = snapshot(team["root"], "t")
        silent = by_id(snap, "b")
        assert silent["state"] == "Не отвечает" and silent["active"] is True
        [asked] = [a for a in snap["attention"] if a["kind"] == "silent"]
        assert asked["task"] == "b" and asked["icon"] == "hourglass"
        analyst = next(a for a in snap["agents"] if a["name"] == "AI-аналитик")
        assert (analyst["state"], analyst["doing"]) == ("Проблема", "перестал отвечать")

    def test_a_fresh_heartbeat_clears_the_silence(self, fresh_team):
        engine.op_heartbeat(fresh_team["root"], "t", "w2", fresh_team["tokens"]["b"])
        snap = snapshot(fresh_team["root"], "t")
        assert by_id(snap, "b")["state"] == "В работе"
        assert [a["kind"] for a in snap["attention"]] == ["blocked"]

    def test_the_silence_limit_is_the_one_of_the_team(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        engine.op_init(other, "t", spec_file(other, {"heartbeat_timeout_s": 60}))
        engine.op_claim(other, "t", "a", "w1")
        set_heartbeat(team_dir(other, "t") / "state.db", "a",
                      "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-120 seconds')")
        assert by_id(snapshot(other, "t"), "a")["state"] == "Не отвечает"

    def test_a_timestamp_without_a_zone_is_read_as_utc_and_a_broken_one_is_no_crash(
            self, fresh_team):
        for value, expected in (("2026-11-25 10:00:00", "Не отвечает"),
                                ("not a date", "В работе")):
            with sqlite3.connect(fresh_team["db"]) as conn:
                conn.execute("UPDATE tasks SET heartbeat_at = ? WHERE id = 'b'", (value,))
            assert by_id(snapshot(fresh_team["root"], "t"), "b")["state"] == expected, value
        with sqlite3.connect(fresh_team["db"]) as conn:
            conn.execute("UPDATE teams SET created_at = 'garbage'")
        assert snapshot(fresh_team["root"], "t")["elapsed_s"] == 0

    def test_the_story_of_the_team_counts_from_its_tasks(self, team):
        assert snapshot(team["root"], "t")["progress"] == {
            "done": 1, "active": 1, "needs_attention": 1, "total": 3, "percent": 33}

    def test_bookkeeping_is_for_the_technical_reader(self, team):
        simple = snapshot(team["root"], "t")
        advanced = snapshot(team["root"], "t", level="advanced")
        advanced_types = {e["type"] for e in advanced["events"]}
        simple_types = {e["type"] for e in simple["events"]}
        assert "workspace_created" in advanced_types
        assert not simple_types & dashboard_data.viewmodel._ADVANCED_ONLY
        assert len(advanced["events"]) > len(simple["events"])

    def test_no_text_contains_a_long_dash(self, team):
        dumped = json.dumps(snapshot(team["root"], "t"), ensure_ascii=False)
        assert "\u2014" not in dumped and "\u2013" not in dumped


class TestAdvancedLevelKeepsTheTechnicalTruth:
    def test_identifiers_metadata_and_hash_beside_every_word(self, team):
        snap = snapshot(team["root"], "t", level="advanced")
        assert by_id(snap, "b")["tech"]["assignee"] == "w2"
        assert by_id(snap, "b")["tech"]["deps"] == ["a"]
        claimed = next(e for e in snap["events"]
                       if e["type"] == "task_claimed" and e["tech"]["task_id"] == "b")
        assert claimed["tech"]["actor_id"] == "w2"
        assert claimed["tech"]["metadata"]["from"] == "pending"
        assert len(claimed["tech"]["hash"]) == 64
        assert {"submissions", "attempts", "usage"} <= set(snap["tech"])
        assert len(snap["tech"]["submissions"]) == 3
        analyst = next(a for a in snap["agents"] if a["name"] == "AI-аналитик")
        assert analyst["tech"]["agent_id"] == "w2"

    def test_the_journal_half_of_a_manual_acceptance_is_named_as_a_record(self, team):
        lines = titles(snapshot(team["root"], "t", level="advanced"))
        assert "Запись о принятии вручную" in lines


class TestEventsWindowAndCursor:
    def test_events_are_the_newest_ones_told_oldest_first(self, team):
        whole = snapshot(team["root"], "t", level="advanced")["events"]
        tail = snapshot(team["root"], "t", level="advanced", event_limit=3)["events"]
        seqs = [e["seq"] for e in whole]
        assert seqs == sorted(seqs)
        assert [e["seq"] for e in tail] == seqs[-3:]

    def test_after_returns_only_newer_events_and_keeps_the_cursor(self, team):
        whole = snapshot(team["root"], "t", level="advanced")["events"]
        cut = whole[-3]["seq"]
        page = snapshot(team["root"], "t", level="advanced", event_after=cut)
        assert [e["seq"] for e in page["events"]] == [e["seq"] for e in whole[-2:]]
        assert page["last_seq"] == whole[-1]["seq"]

    def test_the_cursor_moves_past_events_the_reader_is_not_shown(self, fresh_team):
        # The newest journal events are bookkeeping: nothing new for the
        # reader, but the cursor must not ask for the same page forever.
        engine.op_usage(fresh_team["root"], "t", "b", "w2", _notification(fresh_team["root"]))
        simple = snapshot(fresh_team["root"], "t")
        assert simple["events"][-1]["type"] != "token_usage_recorded"
        newest = snapshot(fresh_team["root"], "t", level="advanced")["last_seq"]
        assert newest > simple["events"][-1]["seq"]
        assert simple["last_seq"] == newest
        empty = snapshot(fresh_team["root"], "t", event_after=newest)
        assert empty["events"] == [] and empty["last_seq"] == newest

    def test_a_limit_of_one_hides_nothing_from_the_cursor(self, team):
        page = snapshot(team["root"], "t", event_limit=1)
        assert len(page["events"]) <= 1
        assert page["last_seq"] == snapshot(team["root"], "t", level="advanced")["last_seq"]


def _notification(root: Path) -> Path:
    path = root / "notif.txt"
    path.write_text("<usage>\nsubagent_tokens: 1234\ntool_uses: 3\nduration_ms: 900\n"
                    "</usage>", encoding="utf-8")
    return path


class TestCost:
    def test_a_stored_cost_is_reported_and_a_missing_price_is_unknown(self, fresh_team):
        engine.op_usage(fresh_team["root"], "t", "b", "w2", _notification(fresh_team["root"]))
        # A notification carries no model: the price is unknown, not zero.
        cost = snapshot(fresh_team["root"], "t")["cost"]
        assert cost == {"tokens": 1234, "usd": 0.0, "unknown_records": 1,
                        "complete": False, "quality": "unknown",
                        "by_task": {}, "by_role": {}}   # nothing known to break down
        with sqlite3.connect(fresh_team["db"]) as conn:
            stored = conn.execute("SELECT cost_usd, cost_quality FROM token_usage").fetchone()
        assert stored == (None, "unknown")

    def test_known_spend_is_broken_down_by_task_and_by_role_in_words(self, fresh_team):
        engine.op_tokens(fresh_team["root"], "t", "b", 1_000_000, "w2", "glm-5.3")
        engine.op_tokens(fresh_team["root"], "t", "a", 1_000_000, "analyst", "glm-5.3")
        cost = snapshot(fresh_team["root"], "t")["cost"]
        one = pytest.approx(1.8)
        assert cost["by_task"] == {"Собрать отчёт": one, "Проверить отчёт": one}
        # "analyst" is nobody's id here: its spend is counted, named by nobody.
        assert cost["by_role"] == {"AI-аналитик": one}
        assert cost["usd"] == pytest.approx(3.6)


class TestReadingCannotWrite:
    def test_the_connection_refuses_to_write(self, team):
        with pytest.raises(DashboardDataError):
            with _readonly(team_dir(team["root"], "t")) as conn:
                conn.execute("DELETE FROM tasks")

    def test_snapshots_leave_the_state_files_as_they_were(self, team):
        wal = Path(str(team["db"]) + "-wal")
        before = (sha(team["db"]), sha(wal))
        for level in ("simple", "advanced"):
            for _ in range(5):
                snapshot(team["root"], "t", level=level)
        assert (sha(team["db"]), sha(wal)) == before

    def test_a_missing_team_and_a_broken_database_are_named(self, fresh_team):
        with pytest.raises(DashboardDataError, match="no team state"):
            snapshot(fresh_team["root"], "nobody")
        broken = team_dir(fresh_team["root"], "broken")
        broken.mkdir()
        (broken / "state.db").write_bytes(b"this is not a database" * 50)
        with pytest.raises(DashboardDataError):
            snapshot(fresh_team["root"], "broken")
        with pytest.raises(DashboardDataError, match="no team state"):
            snapshot(fresh_team["root"] / "elsewhere", "t")


class TestTheWordsOfTheSpec:
    def test_the_project_is_named_and_described_by_its_spec(self, tmp_path):
        root = tmp_path / "Исследование рынка"      # an unnamed team takes the folder name
        (root / "out").mkdir(parents=True)
        spec_file(root)
        engine.op_init(root, "t", spec_file(root))
        plain = snapshot(root, "t")["project"]
        assert plain == {"name": "Исследование рынка", "goal": "", "started_at": plain["started_at"]}
        assert "root" not in plain
        assert snapshot(root, "t", level="advanced")["project"]["root"] == str(root.resolve())

    def test_a_goal_and_a_name_written_in_the_spec_are_shown_as_they_are(self, tmp_path):
        spec_file(tmp_path)
        path = tmp_path / "spec.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["title"] = "Исследование рынка"
        spec["goal"] = "Сравнить конкурентов и подготовить рекомендации"
        spec["tasks"][0]["description"] = "Собрать данные по трём конкурентам"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        engine.op_init(tmp_path, "t", path)
        snap = snapshot(tmp_path, "t")
        assert snap["project"]["name"] == "Исследование рынка"
        assert snap["project"]["goal"] == "Сравнить конкурентов и подготовить рекомендации"
        assert snap["tasks"][0]["description"] == "Собрать данные по трём конкурентам"


class TestSpecialists:
    def test_the_busy_come_first_and_everyone_is_named_by_role(self, team):
        agents = snapshot(team["root"], "t")["agents"]
        # Two executors (the first wears two hats: tasks a and c), both in
        # trouble, then the free checker.
        assert [(a["name"], a["state"]) for a in agents] == [
            ("AI-аналитик", "Проблема"), ("AI-редактор документации", "Проблема"),
            ("AI-проверяющий", "Свободен")]
        analyst = agents[0]
        assert analyst["does"] == "Собирает и сравнивает данные, делает выводы."
        assert analyst["tasks"] == [{"id": "b", "title": TITLES["b"], "role": "AI-аналитик",
                                     "state": "Не отвечает", "kind": "problem"}]   # silent, not "in work"
        assert analyst["worked_s"] is not None and analyst["checks"] is None
        assert agents[1]["tasks"] == [
            {"id": "a", "title": TITLES["a"], "role": "AI-редактор документации",
             "state": "Принято", "kind": "done"},     # one executor, two hats
            {"id": "c", "title": TITLES["c"], "role": "AI-инженер",
             "state": "Нужно ваше внимание", "kind": "problem"}]
        for role in agents:
            assert role["does"] and "id" not in role and "tech" not in role

    def test_the_checker_is_a_specialist_once_there_is_something_to_check(self, tmp_path):
        spec_file(tmp_path)
        engine.op_init(tmp_path, "t", spec_file(tmp_path))
        assert [a["name"] for a in snapshot(tmp_path, "t")["agents"]] == [
            "AI-аналитик", "AI-редактор документации"]
        token = engine.op_claim(tmp_path, "t", "a", "w1")["execution_token"]
        engine.op_submit(tmp_path, "t", "a", "w1", token)
        waiting = snapshot(tmp_path, "t")["agents"]
        # The one checking comes first, then the one waiting, then the free.
        assert [(a["name"], a["state"]) for a in waiting] == [
            ("AI-проверяющий", "Проверяет"), ("AI-редактор документации", "Ожидает"),
            ("AI-аналитик", "Свободен")]
        checker = waiting[0]
        assert checker["doing"] == f"проверяет: {TITLES['a']}"
        assert (checker["checks"], checker["completed"]) == (0, 0)

    def test_what_the_checker_did_is_counted_and_its_ids_stay_in_the_technical_level(self, team):
        simple = snapshot(team["root"], "t")["agents"][-1]
        # Three checks; the one result that was accepted was accepted by the
        # lead's decision, so it is not counted as the checker's.
        assert simple["name"] == "AI-проверяющий" and (simple["checks"], simple["completed"]) == (3, 0)
        advanced = snapshot(team["root"], "t", level="advanced")["agents"][-1]
        assert advanced["tech"] == {"verifiers": ["verifier"]}

    def test_every_event_points_at_the_card_of_who_did_it(self, team):
        snap = snapshot(team["root"], "t")
        names = [a["name"] for a in snap["agents"]]
        said = {names[e["by"]]: True for e in snap["events"] if e.get("by") is not None}
        assert set(said) == {"AI-редактор документации", "AI-проверяющий", "AI-аналитик"}
        # The lead and the system are not specialists: their lines point at nobody.
        assert all(e.get("by") is None for e in snap["events"]
                   if e["specialist"] in ("Руководитель команды", "Система"))


class TestSpecialistsOfOneRole:
    def test_two_executors_with_one_role_are_numbered_on_cards_and_in_the_feed(self, tmp_path):
        spec_file(tmp_path)
        path = tmp_path / "spec.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["tasks"][1]["lens"] = "docs"            # b gets the role of a: AI-редактор документации
        spec["tasks"][1]["deps"] = []                 # and can start at once
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        engine.op_init(tmp_path, "t", path)
        snap = snapshot(tmp_path, "t")
        assert [a["name"] for a in snap["agents"]] == [
            "AI-редактор документации 1", "AI-редактор документации 2"]   # no checker yet
        engine.op_claim(tmp_path, "t", "a", "w1")
        engine.op_claim(tmp_path, "t", "b", "w2")
        snap = snapshot(tmp_path, "t")
        said = {e["task"]: e["specialist"] for e in snap["events"] if e["type"] == "task_claimed"}
        assert said == {TITLES["a"]: "AI-редактор документации 1",
                        TITLES["b"]: "AI-редактор документации 2"}     # by number on their own tasks
        assert snap["cost"]["by_role"] == {}       # and spend is still counted per role


class TestWhatAStageSays:
    def view(self, snap, task_id):
        return next(t for t in snap["tasks"] if t["id"] == task_id)

    def test_expected_results_criteria_and_the_next_step_in_words(self, team):
        snap = snapshot(team["root"], "t")
        blocked = self.view(snap, "c")
        assert blocked["expected"] == ["out/c.md"]
        assert blocked["criteria"] == [{"text": "риски", "how": "решение человека"}]
        assert blocked["next_step"] == "Ждёт вашего решения"
        assert blocked["checkpoints"] == {"started": True, "submitted": True, "accepted": False}
        accepted = self.view(snap, "a")
        assert accepted["next_step"] == "Готово, результат принят"
        assert accepted["checkpoints"] == {"started": True, "submitted": True, "accepted": True}
        assert accepted["last_event"]["title"] == "Этап завершён"
        assert accepted["worked_s"] >= 0
        assert self.view(snap, "b")["next_step"].startswith("Ждёт ответа исполнителя")   # silent
        # No description was written for these tasks: nothing is invented.
        assert accepted["description"] == ""

    @pytest.mark.parametrize("task_id, words", [
        ("a", "Готово, результат принят"),
        ("b", "Ждёт ответа исполнителя; без ответа система вернёт задачу в очередь"),
        ("c", "Ждёт вашего решения"),
    ])
    def test_the_next_step_of_every_task_of_the_team(self, team, task_id, words):
        assert self.view(snapshot(team["root"], "t"), task_id)["next_step"] == words

    def test_how_often_each_result_was_handed_over(self, team):
        snap = snapshot(team["root"], "t")
        result = {t["id"]: (t["result"]["handed_over_count"], t["result"]["ready"], t["result"]["files"])
                  for t in snap["tasks"]}
        assert result["a"] == (1, True, ["out/a.md"])
        assert result["c"] == (2, False, ["out/c.md"])   # the stray README.md is a violation, not a result
        assert result["b"] == (0, False, [])          # never handed over

    def test_a_machine_check_and_judgement_of_content_are_told_apart(self, team):
        criteria = json.dumps([
            {"id": "AC-1", "description": "tests pass", "type": "test", "verification": "true"},
            {"id": "AC-2", "description": "reads well", "type": "documentation"},
            {"id": "AC-3", "description": "owner is happy", "type": "manual"}])
        with sqlite3.connect(team["db"]) as conn:
            conn.execute("UPDATE tasks SET acceptance_json = ? WHERE id = 'a'", (criteria,))
        how = [c["how"] for c in self.view(snapshot(team["root"], "t"), "a")["criteria"]]
        assert how == ["автоматическая проверка: запускается команда",
                       "оценка содержания проверяющим", "решение человека"]


class TestTheClock:
    def test_it_runs_while_the_run_is_open(self, team):
        snap = snapshot(team["root"], "t")
        assert snap["finished"] is False
        assert 0 <= snap["elapsed_s"] < 120, snap["elapsed_s"]

    def test_it_stops_with_the_run(self, fresh_team):
        with sqlite3.connect(fresh_team["db"]) as conn:
            conn.execute("UPDATE runs SET status = 'finished', finished_at ="
                         " strftime('%Y-%m-%dT%H:%M:%fZ', started_at, '+100 seconds')")
        snap = snapshot(fresh_team["root"], "t")
        assert snap["finished"] is True and snap["elapsed_s"] == 100
        assert snap["run"]["state"] == "Завершено"


class TestWhileTheEngineWrites:
    def test_snapshots_stay_whole_under_a_writer(self, fresh_team):
        stop = threading.Event()
        failures: list[Exception] = []
        cursor = {"last": 0}

        def writer():
            while not stop.is_set():
                try:
                    engine.op_heartbeat(fresh_team["root"], "t", "w2", fresh_team["tokens"]["b"])
                except Exception as exc:  # reported by the test, never swallowed
                    failures.append(exc)
                    return

        def reader():
            while not stop.is_set():
                try:
                    snap = snapshot(fresh_team["root"], "t")
                    # Whole on every read: nothing refers to a task that is
                    # not there, and the cursor never goes back.
                    assert all(e["task"] in (None, *TITLES.values()) for e in snap["events"])
                    assert len(snap["tasks"]) == 3
                    assert snap["last_seq"] >= cursor["last"]
                    cursor["last"] = snap["last_seq"]
                except Exception as exc:
                    failures.append(exc)
                    return

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        try:
            time.sleep(1.5)
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=10)
        assert failures == []
        assert cursor["last"] > 0


def test_teams_dir_name_matches_the_engine():
    assert dashboard_data.TEAMS_DIR == engine.team_dir_for(Path("/x"), "t").parent.name
