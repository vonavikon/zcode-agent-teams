"""The reader's language (v2.1 spec 1.2, 2.1, 2.2, 4.5).

viewmodel is the single place where engine state becomes words. These
tests pin the promises the dashboard makes to a non-technical reader:
the simple level shows no raw identifier, a manual acceptance never
reads as a passed check, the counts in the texts come from the task,
and the model covers every status and event the engine can write.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_teams import engine, events, statemachine, viewmodel
from agent_teams.viewmodel import (_times, agent_view, attention_items, cost_view,
                                   event_view, progress, task_view)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
PAGE = Path(engine.__file__).parent / "dashboard" / "app.js"
RAW_IDS = {"researcher", "analyst", "verifier", "lead", "recovery", "worker-1"}


def iso(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def task(**fields) -> dict:
    base = {"id": "t1", "title": "Собрать отчёт", "status": "pending",
            "assignee": "researcher", "owner": None, "lens": "docs",
            "deps": [], "attempt": 0, "verification_failures": 0,
            "restarts": 0, "claimed_at": None, "heartbeat_at": None,
            "completed_at": None, "blocked_reason": None, "blocked_at": None,
            "metadata": {}}
    return {**base, **fields}


def event(event_type: str, *, actor: str = "researcher", task_id: str | None = "t1",
          seq: int = 1, **metadata) -> dict:
    return {"seq": seq, "event_id": f"e{seq}", "run_id": "r1", "task_id": task_id,
            "actor_id": actor, "event_type": event_type, "timestamp": iso(0),
            "hash": "h" * 64, "metadata": metadata}


def strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)
    elif isinstance(node, str):
        yield node


TITLES = {"t1": "Собрать отчёт", "t2": "Проверить отчёт"}
ROLES = {("researcher", None): "AI-редактор документации",
         ("analyst", None): "AI-аналитик", ("analyst", "t2"): "AI-аналитик производительности"}


class TestVocabularyCoversTheEngine:
    def test_every_task_status_has_a_face_and_none_is_invented(self):
        written = {status.value for status in statemachine.TaskStatus}
        assert written == set(viewmodel.TASK_STATE)

    def test_every_state_carries_text_and_an_icon(self):
        for table in (viewmodel.TASK_STATE, viewmodel.AGENT_STATE, viewmodel.RUN_STATE):
            for name, state in table.items():
                assert state["label"], name
                assert state["hint"], name
        for name, state in viewmodel.TASK_STATE.items():
            assert state["icon"] and state["kind"], name

    def test_every_event_type_is_translated(self):
        assert set(events.EVENT_TYPES) == set(viewmodel.EVENT_TEXT)

    def test_every_event_icon_exists_on_the_page(self):
        # The page draws an icon by name; a name it does not have would
        # silently fall back to an empty circle.
        icons = set(re.findall(r"^\s{4}(\w+): '<", PAGE.read_text(encoding="utf-8"),
                               re.MULTILINE))
        used = {mark for _, mark in viewmodel._EVENT_MARKS.values()}
        used |= {state["icon"] for state in viewmodel.TASK_STATE.values()}
        used |= {state["icon"] for state in viewmodel.AGENT_STATE.values()}
        assert used <= icons, used - icons

    def test_every_event_has_exactly_one_mark(self):
        assert set(viewmodel._EVENT_MARKS) == set(viewmodel.EVENT_TEXT)

    def test_run_states_are_the_ones_the_engine_writes(self):
        source = Path(engine.__file__).read_text(encoding="utf-8")
        written = set(re.findall(r'update_run\([^)]*?status="([a-z_]+)"', source, re.S))
        # A run is born "active" and only then moves through update_run.
        created = set(re.findall(r'"started_at": now_iso\(\), "status": "([a-z_]+)"',
                                 source))
        assert created == {"active"}
        assert written | created == set(viewmodel.RUN_STATE)

    def test_bookkeeping_is_hidden_only_in_the_simple_level(self):
        assert viewmodel._ADVANCED_ONLY <= set(viewmodel.EVENT_TEXT)


class TestSimpleLevelNamesNobodyById:
    def test_task_names_the_role_and_keeps_the_id_out_of_the_simple_view(self):
        view = task_view(task(), level="simple", titles=TITLES)
        assert view["role"] == "AI-редактор документации"
        assert "researcher" not in set(strings(view))
        assert not {"specialist", "owner", "assignee"} & set(view)

    def test_advanced_view_keeps_the_identifiers_beside_the_words(self):
        view = task_view(task(), level="advanced", titles=TITLES)
        assert view["tech"]["assignee"] == "researcher"
        assert view["role"] == "AI-редактор документации"

    def test_an_unknown_lens_is_a_generic_specialist_never_a_raw_word(self):
        assert task_view(task(lens="whatever-lens"))["role"] == "AI-специалист"
        assert task_view(task(lens=""))["role"] == "AI-специалист"

    def test_agent_view_has_no_id_in_the_simple_level(self):
        # An agent is named by its tasks; with none it is a generic specialist.
        agent = {"id": "analyst", "role": "executor", "status": "idle",
                 "heartbeat_at": None, "restarts": 0}
        view = agent_view(agent, [task()], now=NOW)
        assert view["name"] == "AI-специалист"
        assert "analyst" not in set(strings(view))
        named = agent_view(agent, [task(assignee="analyst", lens="behavior")], now=NOW)
        assert named["name"] == "AI-аналитик"
        assert agent_view(agent, [task()], level="advanced", now=NOW)["tech"]["agent_id"] == "analyst"

    def test_on_its_own_task_a_specialist_wears_that_tasks_role(self):
        # One executor, two lenses: the feed must not call it by its other task.
        own = event_view(event("task_claimed", actor="analyst", task_id="t2"),
                         titles=TITLES, roles=ROLES)
        assert own["specialist"] == "AI-аналитик производительности"
        other = event_view(event("task_claimed", actor="analyst", task_id="t1"),
                           titles=TITLES, roles=ROLES)
        assert other["specialist"] == "AI-аналитик"
        loose = event_view(event("agent_finished", actor="analyst", task_id=None), roles=ROLES)
        assert loose["specialist"] == "AI-аналитик"

    @pytest.mark.parametrize("etype, actor, expected", [
        ("task_claimed", "researcher", "AI-редактор документации"),
        ("verification_passed", "verifier", "AI-проверяющий"),
        ("verification_failed", "analyst", "AI-проверяющий"),
        ("task_blocked", "verifier", "AI-проверяющий"),
        ("task_released", "lead", "Руководитель команды"),
        ("task_released", "recovery", "Система"),
        ("task_claimed", "someone-new", "AI-специалист"),
    ])
    def test_the_actor_is_named_by_function_or_role(self, etype, actor, expected):
        item = event_view(event(etype, actor=actor, failures=2), titles=TITLES, roles=ROLES)
        assert item["specialist"] == expected
        assert actor not in set(strings(item))

    def test_the_event_names_the_task_by_title(self):
        item = event_view(event("task_claimed"), titles=TITLES, roles=ROLES)
        assert item["task"] == "Собрать отчёт"
        assert "t1" not in set(strings(item))

    def test_advanced_event_keeps_ids_and_the_full_metadata(self):
        raw = event("task_claimed", assignee="researcher", **{"from": "pending"})
        item = event_view(raw, level="advanced", titles=TITLES, roles=ROLES)
        assert item["tech"]["actor_id"] == "researcher"
        assert item["tech"]["task_id"] == "t1"
        assert item["tech"]["metadata"]["from"] == "pending"
        assert item["tech"]["hash"] == "h" * 64

    def test_simple_event_carries_no_metadata(self):
        item = event_view(event("task_claimed", assignee="researcher"), titles=TITLES)
        assert "tech" not in item and "assignee" not in set(strings(item))

    def test_dependencies_become_titles_with_the_map_and_stay_ids_without(self):
        blocked = task(deps=["t1"])
        assert task_view(blocked, titles=TITLES)["deps"] == ["Собрать отчёт"]
        assert task_view(blocked)["deps"] == ["t1"]
        assert task_view(blocked, level="advanced", titles=TITLES)["tech"]["deps"] == ["t1"]

    def test_an_unknown_dependency_is_left_out_of_the_simple_view(self):
        assert task_view(task(deps=["gone"]), titles=TITLES)["deps"] == [""]


class TestManualAcceptance:
    OVERRIDE = event("verification_passed", actor="lead", override=True,
                     override_reason="demo", submission_id="s1")
    JOURNAL = event("verification_passed", actor="verifier", attempt=1,
                    submission_id="s1", seq=2)

    def test_the_decision_is_reworded_and_marked_manual(self):
        item = event_view(self.OVERRIDE, titles=TITLES)
        assert "вручную" in item["title"] and "без отчёта о проверке" in item["title"]
        assert item["kind"] == "manual"
        assert "Руководитель команды" in item["detail"]

    def test_the_journal_half_is_hidden_in_the_simple_level(self):
        # Same type and shape as a passed check; no check was made.
        assert event_view(self.JOURNAL, titles=TITLES, after_override=True) is None

    def test_the_journal_half_is_named_as_a_record_in_the_advanced_level(self):
        item = event_view(self.JOURNAL, level="advanced", titles=TITLES,
                          after_override=True)
        assert item["kind"] == "manual"
        assert item["title"] == "Запись о принятии вручную"
        assert "проверка не выполнялась" in item["detail"]

    def test_a_real_pass_stays_a_pass(self):
        item = event_view(self.JOURNAL, titles=TITLES)
        assert item["title"] == "Проверка пройдена" and item["kind"] == "done"


class TestEventsTellTheTruth:
    def test_the_transitional_block_of_a_lost_executor_is_left_out(self):
        # Recovery blocks a task and releases it in one step: nothing is
        # waited from the reader, the alarm would be false.
        lost = event("task_blocked", reason="executor lost: heartbeat timeout")
        assert event_view(lost, titles=TITLES) is None
        record = event_view(lost, level="advanced", titles=TITLES)
        assert record["title"] == "Исполнитель потерян (запись в журнале)"
        assert "не требуется" in record["detail"]

    def test_a_block_at_the_verification_limit_stays(self):
        blocked = event("task_blocked", reason="2 failed verification rounds (limit 2)",
                        failures=2)
        item = event_view(blocked, titles=TITLES)
        assert "нужно решение человека" in item["title"]
        assert item["detail"] == "Результат 2 раза не прошёл проверку"

    def test_a_write_outside_the_zone_is_not_a_clash_between_results(self):
        outside = event_view(event("workspace_conflict", violations=["README.md"]), titles=TITLES)
        assert outside["title"] == "Изменения вне разрешённой зоны"
        assert "README.md" in outside["detail"]
        clash = event_view(event("workspace_conflict", conflicts=["a.md"]), titles=TITLES)
        assert clash["title"] == "Не удалось объединить результаты"

    def test_the_remark_of_the_checker_reaches_the_feed(self):
        failed = event("verification_failed", feedback="нет оценок вероятности")
        assert event_view(failed, titles=TITLES)["detail"] == \
            "Замечания проверяющего: нет оценок вероятности"

    def test_a_long_remark_is_cut_not_dropped(self):
        item = event_view(event("verification_failed", feedback="x" * 5000), titles=TITLES)
        assert item["detail"].endswith("x" * viewmodel.REMARK_LIMIT)

    def test_bookkeeping_is_shown_to_the_technical_reader_only(self):
        for etype in sorted(viewmodel._ADVANCED_ONLY):
            assert event_view(event(etype), titles=TITLES) is None, etype
            assert event_view(event(etype), level="advanced", titles=TITLES) is not None, etype

    def test_an_unknown_event_type_means_nothing_to_a_reader(self):
        assert event_view(event("not_an_event")) is None

    def test_events_come_with_a_kind_and_an_icon(self):
        item = event_view(event("task_cancelled", actor="lead"), titles=TITLES)
        assert (item["kind"], item["icon"]) == ("normal", "minus")
        assert event_view(event("verification_failed"))["kind"] == "problem"


class TestSilence:
    def test_an_executor_silent_beyond_the_limit_is_not_shown_as_working(self):
        view = task_view(task(status="in_progress", owner="researcher",
                              claimed_at=iso(5000), heartbeat_at=iso(901)), now=NOW)
        assert view["state"] == "Не отвечает" and view["kind"] == "problem"
        assert view["icon"] == "alert" and view["active"] is True

    def test_within_the_limit_it_is_still_in_work(self):
        view = task_view(task(status="in_progress", owner="researcher",
                              claimed_at=iso(5000), heartbeat_at=iso(899)), now=NOW)
        assert view["state"] == "В работе" and view["kind"] == "busy"
        assert view["active"] is True and view["silent"] is False

    def test_the_limit_is_the_one_the_team_is_configured_with(self):
        late = task(status="in_progress", owner="researcher", heartbeat_at=iso(200))
        assert task_view(late, now=NOW, threshold_s=100)["state"] == "Не отвечает"
        assert task_view(late, now=NOW, threshold_s=1000)["state"] == "В работе"

    def test_only_started_tasks_can_be_silent(self):
        assert task_view(task(claimed_at=iso(99999)), now=NOW)["silent_s"] is None
        assert task_view(task(status="completed", claimed_at=iso(99999)), now=NOW)["silent_s"] is None
        waiting = task(status="awaiting_verification", heartbeat_at=iso(30))
        assert task_view(waiting, now=NOW)["silent_s"] == 30

    def test_a_missing_heartbeat_falls_back_to_the_start(self):
        started = task(status="in_progress", claimed_at=iso(45))
        assert task_view(started, now=NOW)["silent_s"] == 45

    def test_an_unreadable_timestamp_is_no_silence_rather_than_a_number(self):
        broken = task(status="in_progress", heartbeat_at="not a date")
        assert task_view(broken, now=NOW)["silent_s"] is None


class TestBlockedExplanationCountsFromTheTask:
    def blocked(self, **fields) -> dict:
        return task(status="blocked", blocked_reason="N failed verification rounds (limit N)",
                    blocked_at=iso(600), **fields)

    def remarks(self, blocked) -> dict:
        [item] = attention_items([blocked], None, now=NOW)
        return item

    @pytest.mark.parametrize("failures, expected", [
        (1, "1 раз"), (2, "2 раза"), (5, "5 раз"), (11, "11 раз"),
        (21, "21 раз"), (22, "22 раза"), (None, "несколько раз"),
    ])
    def test_the_count_in_the_text_is_the_count_of_the_task(self, failures, expected):
        # The feed line and the attention card both count from the data.
        blocked = event("task_blocked", failures=failures,
                        reason="2 failed verification rounds (limit 2)")
        line = event_view(blocked, titles=TITLES)["detail"]
        assert line == f"Результат {expected} не прошёл проверку"
        what = self.remarks(self.blocked(verification_failures=failures))["what"]
        if failures != 1:  # one failure is told in the singular, tested below
            assert f"передавал результат {expected}" in what, what

    def test_the_attention_card_is_found_by_the_task_title(self):
        item = self.remarks(self.blocked(verification_failures=3))
        assert item["title"] == "Собрать отчёт"
        assert "передавал результат 3 раза" in item["what"]

    def test_no_count_is_hard_coded_in_the_texts(self):
        source = Path(viewmodel.__file__).read_text(encoding="utf-8")
        for word in ("дважды", "оба раза", "две попытки"):
            assert word not in source.split("def _blocked_explanation")[1].split(
                "def attention_items")[0], word

    def test_one_failure_is_told_in_the_singular(self):
        what = self.remarks(self.blocked(verification_failures=1))["what"]
        assert what.startswith("Исполнитель передал результат, и проверяющий нашёл недостатки.")
        assert "Новых попыток система сама не даёт" in what

    def test_the_restart_limit_counts_one_more_than_the_counter(self):
        # The counter stops at the limit; the block comes on the next timeout.
        item = attention_items([task(
            status="blocked", blocked_reason="agent restart limit reached after timeouts",
            verification_failures=0, restarts=2)], None, now=NOW)[0]
        assert "Исполнитель задачи 3 раза не ответил" in item["what"]

    def test_the_remark_of_the_checker_is_attached(self):
        [item] = attention_items([self.blocked()], None, now=NOW,
                                 remarks={"t1": "по-прежнему нет оценок"})
        assert item["remarks"] == "по-прежнему нет оценок"
        assert attention_items([self.blocked()], None, now=NOW)[0]["remarks"] == ""

    def test_an_unknown_reason_admits_it(self):
        [item] = attention_items([task(status="blocked", blocked_reason="something new")],
                                 None, now=NOW)
        assert item["title"] == "Собрать отчёт"
        assert "не может продолжаться без решения человека" in item["what"]


class TestAttention:
    def test_a_healthy_team_needs_nobody(self):
        running = task(status="in_progress", owner="researcher", heartbeat_at=iso(5))
        assert attention_items([task(), running], {"status": "active"}, now=NOW) == []

    def test_blocked_task_integration_and_apply_wait_for_a_person(self):
        blocked = task(status="blocked", blocked_reason="x verification y",
                       verification_failures=2, blocked_at=iso(10))
        items = attention_items(
            [blocked], {"status": "integration_failed", "finished_at": iso(9)}, now=NOW)
        assert [i["kind"] for i in items] == ["blocked", "integration"]
        apply = attention_items([task()], {"status": "apply_pending"}, now=NOW)
        assert [i["kind"] for i in apply] == ["apply"]

    @pytest.mark.parametrize("status", ["integration_failed", "apply_pending", "active"])
    def test_every_item_answers_the_five_questions_and_names_its_icon(self, status):
        run = {"status": status, "finished_at": iso(1)}
        for item in attention_items([task(status="blocked", blocked_reason="conflict")], run,
                                    now=NOW):
            for key in ("what", "why", "tried", "action", "if_nothing"):
                assert item[key].strip(), (item["kind"], key)
            assert item["icon"] and item["kind"] and item["title"]

    def test_a_silent_executor_is_asked_about_only_beyond_the_limit(self):
        working = task(status="in_progress", owner="researcher", heartbeat_at=iso(1000))
        late = attention_items([working], None, now=NOW)
        assert [i["kind"] for i in late] == ["silent"]
        assert late[0]["icon"] == "hourglass"
        assert late[0]["if_nothing"]  # what happens by itself is spelled out
        assert attention_items([working], None, now=NOW, threshold_s=5000) == []

    def test_a_block_and_a_ready_result_carry_different_icons(self):
        blocked = attention_items([task(status="blocked", blocked_reason="verification")],
                                  None, now=NOW)[0]
        ready = attention_items([task()], {"status": "apply_pending"}, now=NOW)[0]
        assert blocked["icon"] == "alert" and ready["icon"] == "check"


class TestTheWordsOfAStage:
    def test_what_is_expected_and_how_it_is_judged(self):
        view = task_view(task(spec={"description": "Собрать данные"}, files_write=["out/a.md"],
                              acceptance=[{"description": "tests pass", "type": "test"},
                                          {"description": "reads well", "type": "documentation"},
                                          {"description": "a string criterion"}]), now=NOW)
        assert view["description"] == "Собрать данные"
        assert view["expected"] == ["out/a.md"]
        assert view["criteria"] == [
            {"text": "tests pass", "how": "автоматическая проверка: запускается команда"},
            {"text": "reads well", "how": "оценка содержания проверяющим"},
            {"text": "a string criterion", "how": "решение человека"}]
        assert task_view(task(), now=NOW)["description"] == ""

    @pytest.mark.parametrize("status, words", [
        ("completed", "Готово, результат принят"), ("cancelled", "Задача снята"),
        ("blocked", "Ждёт вашего решения"),
        ("awaiting_verification", "Идёт проверка качества"),
        ("in_progress", "Специалист работает"), ("pending", "Ожидает исполнителя"),
    ])
    def test_the_next_step_of_every_state(self, status, words):
        assert task_view(task(status=status), now=NOW)["next_step"] == words

    def test_a_silent_executor_does_not_get_next_step_working(self):
        late = task(status="in_progress", owner="researcher", heartbeat_at=iso(901))
        assert task_view(late, now=NOW)["next_step"].startswith("Ждёт ответа исполнителя")
        assert task_view(late, now=NOW, threshold_s=5000)["next_step"] == "Специалист работает"

    def test_a_task_waits_for_the_parents_that_are_not_done(self):
        by_id = {"done": task(status="completed", title="Готово"),
                 "off": task(status="cancelled", title="Снято"),
                 "work": task(status="in_progress", title="В работе"),
                 "wait": task(status="pending", title="В очереди")}
        view = task_view(task(deps=["done", "off", "work", "wait", "gone"]), now=NOW, by_id=by_id)
        assert view["next_step"] == "Ждёт завершения: В работе, В очереди, другой этап"
        assert by_id["done"]["title"] == "Готово"

    @pytest.mark.parametrize("status, started, expected", [
        ("pending", None, [False, False, False]),
        ("in_progress", iso(500), [True, False, False]),
        ("in_progress", None, [False, False, False]),      # never claimed: no checkpoint
        ("awaiting_verification", iso(500), [True, True, False]),
        ("completed", iso(500), [True, True, True]),
    ])
    def test_only_the_checkpoints_the_engine_recorded(self, status, started, expected):
        view = task_view(task(status=status, claimed_at=started), now=NOW)
        assert list(view["checkpoints"].values()) == expected

    def test_time_on_the_task_is_measured_from_what_the_engine_wrote(self):
        done = task_view(task(status="completed", claimed_at=iso(600),
                              completed_at=iso(100)), now=NOW)
        assert done["worked_s"] == 500            # the time it took, not until now
        live = task_view(task(status="in_progress", claimed_at=iso(600)), now=NOW)
        assert live["worked_s"] == 600
        assert task_view(task(status="pending"), now=NOW)["worked_s"] is None


class TestSpecialistCards:
    def test_a_card_says_who_the_specialist_is_and_what_they_do(self):
        agent = {"id": "analyst", "role": "executor", "status": "running",
                 "heartbeat_at": iso(5), "restarts": 0}
        mine = [task(assignee="analyst", owner="analyst", lens="behavior",
                     status="in_progress", claimed_at=iso(500)),
                task(assignee="analyst", lens="behavior", status="pending")]
        view = agent_view(agent, mine, now=NOW)
        assert view["name"] == "AI-аналитик"
        assert view["does"] == "Собирает и сравнивает данные, делает выводы."
        assert view["worked_s"] == 500 and view["silent_s"] == 500   # silence is read from its tasks
        assert view["tasks"] == [
            {"id": "t1", "title": "Собрать отчёт", "role": "AI-аналитик", "state": "В работе", "kind": "busy"},
            {"id": "t1", "title": "Собрать отчёт", "role": "AI-аналитик", "state": "В очереди", "kind": "idle"}]
        assert view["checks"] is None

    def test_every_role_has_its_responsibility(self):
        names = set(viewmodel._ROLE_BY_LENS.values()) | {viewmodel.VERIFIER_ROLE, "AI-специалист"}
        assert all(viewmodel.role_does(name) for name in names)
        assert viewmodel.role_does("a role nobody heard of") == viewmodel.role_does("AI-специалист")

    def test_the_checker_is_counted_by_its_checks(self):
        tasks = [task(status="awaiting_verification", title="Отчёт")]
        attempts = [{"verdict": "pass"}, {"verdict": "fail"}, {"verdict": "pass"}]
        view = viewmodel.verifier_view(tasks, attempts)
        assert view["name"] == "AI-проверяющий"
        assert (view["state"], view["doing"], view["checks"], view["completed"]) == \
            ("Проверяет", "проверяет: Отчёт", 3, 2)
        idle = viewmodel.verifier_view([], attempts)
        assert (idle["state"], idle["doing"]) == ("Свободен", "ждёт результатов для проверки")

    def test_a_decision_of_the_lead_is_not_a_check_of_the_checker(self):
        attempts = [{"verdict": "pass", "task_id": "a"}, {"verdict": "fail", "task_id": "b"},
                    {"verdict": "pass", "task_id": "b"}]
        view = viewmodel.verifier_view([], attempts, manual_tasks={"a"})
        assert (view["checks"], view["completed"], view["total"]) == (2, 1, 2)
        only = viewmodel.verifier_view([], [{"verdict": "pass", "task_id": "a"}], {"a"})
        assert (only["checks"], only["completed"], only["total"]) == (0, 0, 0)

    def test_specialists_of_one_role_are_told_apart_by_a_number(self):
        same = viewmodel.number_duplicates({"w1": "AI-аналитик", "w2": "AI-редактор",
                                            "w3": "AI-аналитик"})
        assert same == {"w1": "AI-аналитик 1", "w2": "AI-редактор", "w3": "AI-аналитик 2"}
        alone = viewmodel.number_duplicates({"w1": "AI-аналитик", "w2": "AI-инженер"})
        assert alone == {"w1": "AI-аналитик", "w2": "AI-инженер"}
        card = agent_view({"id": "w3", "role": "executor", "status": "idle",
                           "heartbeat_at": None, "restarts": 0},
                          [task(assignee="w3", lens="behavior")], now=NOW, name="AI-аналитик 2")
        assert card["name"] == "AI-аналитик 2" and card["does"] == "Собирает и сравнивает данные, делает выводы."

    def test_the_actor_of_a_check_is_the_checker_under_any_id(self):
        passed = event_view(event("verification_passed", actor="v-9"), titles=TITLES, roles=ROLES)
        assert passed["specialist"] == "AI-проверяющий"


class TestProgressAndCost:
    def test_progress_counts_whole_live_tasks_with_equal_weight(self):
        tasks = [task(status="completed"), task(status="in_progress"),
                 task(status="awaiting_verification"), task(),
                 task(status="blocked"), task(status="cancelled")]
        assert progress(tasks) == {"done": 1, "active": 2, "needs_attention": 1,
                                   "total": 5, "percent": 20}

    def test_no_tasks_is_zero_percent_not_a_crash(self):
        assert progress([])["percent"] == 0

    def test_a_stored_cost_is_used_as_is(self):
        # The price of the day the work was done; the table may have moved.
        usage = [{"model": "old-model-x", "total_tokens": 10, "cost_usd": 0.5}]
        assert cost_view(usage) == {"tokens": 10, "usd": 0.5, "unknown_records": 0,
                                    "complete": True, "quality": "estimated"}

    def test_a_record_without_a_stored_cost_is_estimated_here(self):
        usage = [{"model": "glm-5.3", "input_tokens": 1_000_000, "output_tokens": 0}]
        assert cost_view(usage)["usd"] == pytest.approx(0.6)

    def test_an_unknown_cost_is_unknown_never_zero(self):
        view = cost_view([{"model": "mystery", "total_tokens": 5}])
        assert (view["usd"], view["unknown_records"], view["complete"], view["quality"]) == \
            (0.0, 1, False, "unknown")

    def test_partly_known_spend_says_it_is_partly_known(self):
        view = cost_view([{"model": "glm-5.3", "total_tokens": 1_000_000,
                           "input_tokens": 1_000_000, "output_tokens": 0},
                          {"total_tokens": 3}])
        assert view["usd"] > 0 and view["complete"] is False and view["unknown_records"] == 1

    def test_no_records_are_complete_and_zero(self):
        assert cost_view([]) == {"tokens": 0, "usd": 0.0, "unknown_records": 0,
                                 "complete": True, "quality": "unknown"}


@pytest.mark.parametrize("count, word", [
    (1, "1 раз"), (2, "2 раза"), (4, "4 раза"), (5, "5 раз"), (10, "10 раз"),
    (11, "11 раз"), (14, "14 раз"), (21, "21 раз"), (22, "22 раза"),
    (111, "111 раз"), (None, "несколько раз"),
])
def test_russian_count_endings(count, word):
    assert _times(count) == word


def test_no_long_dash_in_any_reader_text():
    # The owner's rule for prose: no em or en dashes, here too.
    source = Path(viewmodel.__file__).read_text(encoding="utf-8")
    body = source.split("TASK_STATE = {", 1)[1]
    assert "\u2014" not in body and "\u2013" not in body


class TestAStageAcceptedByDecision:
    def test_it_is_not_said_to_be_checked_and_accepted(self):
        attempts = [{"verdict": "fail", "feedback": "нет оценок"},
                    {"verdict": "pass", "manual": True}]
        done = task(status="completed", title="Риски", submitted_at="x")
        view = viewmodel.task_view(done, attempts=attempts)
        assert view["state"] == "Принято"
        assert view["state_hint"] == "принято решением руководителя команды, без проверки"
        # A stage that passed a real check keeps the words of a check.
        checked = viewmodel.task_view(done, attempts=[{"verdict": "pass"}])
        assert checked["state_hint"] == "проверено и принято"


class TestTheArchiveNoteOfALostExecutor:
    """The engine writes one lost executor twice: the timeout, then the
    archive of its workspace under the same event type."""

    def note(self):
        return event("agent_timeout", actor="recovery", metadata={"quarantine": "/q", "attempt": 1})

    def test_the_simple_level_shows_one_loss(self):
        assert viewmodel.event_view(self.note(), titles=TITLES, roles=ROLES, archive_note=True) is None

    def test_the_technical_level_shows_it_as_what_it_is(self):
        shown = viewmodel.event_view(self.note(), level="advanced", titles=TITLES, roles=ROLES,
                                     archive_note=True)
        assert shown["bookkeeping"] is True and shown["kind"] == "normal"
        assert shown["title"] == "Рабочая область прежней попытки убрана в архив"
        assert shown["specialist"] == "Система"

    def test_without_the_flag_it_is_what_it_always_was(self):
        shown = viewmodel.event_view(self.note(), titles=TITLES, roles=ROLES)
        assert shown["title"] == "Специалист перестал отвечать" and "bookkeeping" not in shown


class TestTheLiftedBlockIsBookkeeping:
    def test_the_block_recovery_lifts_at_once_is_marked_as_a_note(self):
        lifted = event("task_blocked", actor="recovery", seq=3, reason="executor lost: heartbeat timeout")
        shown = viewmodel.event_view(lifted, level="advanced", titles=TITLES, roles=ROLES)
        assert shown["bookkeeping"] is True
        assert shown["title"] == "Исполнитель потерян (запись в журнале)"

    def test_a_real_stop_is_not_a_note(self):
        stopped = event("task_blocked", actor="lead", seq=9, reason="verification limit reached",
                        failures=2, feedback="нет оценок")
        shown = viewmodel.event_view(stopped, titles=TITLES, roles=ROLES)
        assert "bookkeeping" not in shown and shown["kind"] == "problem"
        advanced = viewmodel.event_view(stopped, level="advanced", titles=TITLES, roles=ROLES)
        assert "bookkeeping" not in advanced

