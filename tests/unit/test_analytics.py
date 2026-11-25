"""What the numbers mean for the owner (v2.1 spec 10 to 13), as pure
functions over what the snapshot read.

Pinned here: a machine check, a judgement of content and a result
accepted by decision are never summed into one number; a result handed
over but not yet checked is neither passed nor failed; a cost that is
not known is never a zero; a budget that is exactly spent is spent; a
recovery that did not work is not shown as one that did.
"""
from __future__ import annotations

import pytest

from agent_teams.analytics import (
    budget_view, integration_view, model_rows, quality_view, recovery_view)


def task(task_id, status="pending", **fields):
    return {"id": task_id, "title": f"Задача {task_id.upper()}", "status": status,
            "claimed_at": None, "submitted_at": None, "restarts": 0,
            "verification_failures": 0, "metadata": {}, **fields}


def check(task_id, n, verdict, feedback="", when="2026-11-25T09:00:00Z", report=None):
    return {"task_id": task_id, "attempt": n, "verdict": verdict, "feedback": feedback,
            "created_at": when, "report": report if report is not None else {}}


def item(seq, event_type, title="", detail="", ts="2026-11-25T09:00:00.000Z"):
    return {"seq": seq, "type": event_type, "title": title, "detail": detail, "ts": ts,
            "kind": "problem"}


class TestQuality:
    def test_nothing_handed_over_yet(self):
        view = quality_view([task("a")], [], set())
        assert view["handed_over"] == 0 and view["story"] == \
            "Результаты ещё не передавались на проверку."
        assert (view["checks"], view["passed"], view["failed"]) == (0, 0, 0)

    def team(self):
        tasks = [
            task("a", "completed", submitted_at="x"),
            task("b", "completed", submitted_at="x"),                 # failed once, then passed
            task("c", "completed", submitted_at="x"),                 # accepted by the lead's decision
            task("d", "awaiting_verification", submitted_at="x"),     # handed over, not checked yet
            task("e", "in_progress"),
        ]
        attempts = [
            check("a", 1, "pass", report={"criteria": [{"passed": True}, {"passed": False}],
                                          "forced_failures": ["test -f out/a.md"]}),
            check("b", 1, "fail", "нет оценок", "2026-11-25T09:10:00Z"),
            check("b", 2, "pass", report={"criteria": [{"passed": True}], "forced_failures": []}),
            check("c", 1, "pass"),                                    # recorded by the engine on an override
        ]
        return tasks, attempts

    def test_three_different_things_are_counted_apart(self):
        tasks, attempts = self.team()
        view = quality_view(tasks, attempts, accepted_by_lead={"c"})
        assert view["handed_over"] == 4            # a, b, c, d; e is still being worked on
        assert view["judged"] == 2 and view["accepted_by_check"] == 2
        assert view["accepted_by_decision"] == 1
        assert (view["checks"], view["passed"], view["failed"]) == (3, 2, 1)   # c's pass is not a check
        assert (view["first_time"], view["reworked"]) == (1, 1)
        assert view["awaited"] == ["Задача D", "Задача E"]
        assert view["machine"] == {"run": 3, "passed": 2, "failed": 1, "forced": 1}  # latest checks only
        assert view["problems"] == [{"task": "b", "remark": "нет оценок", "when": "2026-11-25T09:10:00Z",
                                     "attempt": 1}]

    def test_every_sum_is_broken_down_and_every_check_has_its_mark(self):
        # The same team, with every check stamped in its turn.
        tasks, _ = self.team()
        attempts = [check("a", 1, "pass", when="2026-11-25T09:01:00Z"),
                    check("b", 1, "fail", when="2026-11-25T09:02:00Z"),
                    check("b", 2, "pass", when="2026-11-25T09:03:00Z"),
                    check("c", 1, "pass", when="2026-11-25T09:04:00Z")]
        view = quality_view(tasks, attempts, {"c"}, titles={"a": "Обзор", "b": "Сравнение"})
        # One result, one bucket: how it got as far as it did.
        assert view["split"] == {"first": 1, "reworked": 1, "decision": 1, "awaiting": 1}
        # The checks in the order the answers came back, with the result named.
        assert [(c["verdict"], c["task"], c["attempt"]) for c in view["timeline"]] == [
            ("pass", "Обзор", 1), ("fail", "Сравнение", 1), ("pass", "Сравнение", 2)]
        assert view["problems"][0]["task"] == "Сравнение"
        # A result that passed at first and was then sent back is one that needed rework.
        again = [task("x", "completed", submitted_at="x")]
        checks = [check("x", 1, "pass"), check("x", 2, "fail"), check("x", 3, "pass")]
        assert quality_view(again, checks, set())["split"] == \
            {"first": 0, "reworked": 1, "decision": 0, "awaiting": 0}

    def test_the_story_says_it_in_words(self):
        tasks, attempts = self.team()
        assert quality_view(tasks, attempts, {"c"})["story"] == (
            "1 из 2 результатов прошли проверку с первого раза. Ещё 1 результат пришлось переделать. "
            "1 результат принят решением руководителя команды, без проверки.")
        three = [task(t, "completed", submitted_at="x") for t in "abc"]
        attempts = [check("a", 1, "pass"), check("b", 1, "fail"), check("b", 2, "pass"),
                    check("c", 1, "fail"), check("c", 2, "pass")]
        assert "Ещё 2 результата пришлось переделать." in quality_view(three, attempts, set())["story"]
        clean = quality_view([tasks[0]], attempts[:1], set())
        assert clean["story"] == "1 результат прошёл проверку с первого раза."
        two = [task("a", "completed", submitted_at="x"), task("b", "completed", submitted_at="x")]
        assert quality_view(two, [check("a", 1, "pass"), check("b", 1, "pass")], set())["story"] == \
            "Все 2 результата прошли проверку с первого раза."

    def test_handed_over_but_not_checked_is_neither_passed_nor_failed(self):
        tasks = [task("d", "awaiting_verification", submitted_at="x")]
        view = quality_view(tasks, [], set())
        assert (view["handed_over"], view["judged"], view["first_time"], view["reworked"]) == (1, 0, 0, 0)
        assert view["story"] == "Результаты переданы на проверку, её ответ ещё не пришёл."


class TestBudget:
    def cost(self, usd=0.0, unknown=0, by_task=None):
        return {"usd": usd, "unknown_records": unknown, "complete": unknown == 0,
                "by_task": by_task or {}, "by_role": {}}

    def test_no_budget_and_no_spend(self):
        view = budget_view(self.cost(), {}, [task("a")])
        assert view["state"] == "none" and view["budget_usd"] is None
        assert view["story"] == ("Расходов пока нет. Бюджет не согласован: расходы считаются, "
                                 "предел не установлен.")
        assert view["per_task_text"] == "не определена" and view["per_result_text"] == "не определена"

    def test_the_example_of_the_spec(self):
        tasks = [task("a", "completed", claimed_at="x")]
        view = budget_view(self.cost(7.5), {"budget_usd": 10, "budget_warn_ratio": 0.8}, tasks)
        assert view["state"] == "ok"
        assert view["story"] == "На проект выделено $10.00. Потрачено $7.50. Осталось $2.50."
        assert view["remaining_usd"] == 2.5 and view["share"] == 0.75
        assert view["usd_per_result"] == 7.5 and view["per_result_text"] == "$7.50"

    def test_warning_at_the_agreed_share_and_the_limit(self):
        tasks = []
        config = {"budget_usd": 10, "budget_warn_ratio": 0.8}
        assert budget_view(self.cost(7.99), config, tasks)["state"] == "ok"
        warning = budget_view(self.cost(8), config, tasks)
        assert warning["state"] == "warning" and "почти израсходован" in warning["story"]
        limit = budget_view(self.cost(10), config, tasks)
        assert limit["state"] == "limit" and "Новое" not in limit["story"] and "не запускаются" in limit["story"]
        assert limit["remaining_usd"] == 0.0            # never a negative remainder
        assert budget_view(self.cost(12), config, tasks)["remaining_usd"] == 0.0

    def test_a_budget_that_is_exactly_spent_is_spent(self):
        config = {"budget_usd": 0.0006500000000000001, "budget_warn_ratio": 0.8}
        assert budget_view(self.cost(0.00065), config, [])["state"] == "limit"

    def test_an_unknown_cost_is_not_a_zero(self):
        view = budget_view(self.cost(0.0, unknown=3), {}, [])
        assert view["spent_text"] == "не определена"
        assert "Цена 3 записей об использовании неизвестна" in view["story"]
        view = budget_view(self.cost(7.5, unknown=2), {"budget_usd": 10}, [])
        assert view["spent_text"] == "$7.50" and "цена части записей неизвестна" in view["story"]
        assert view["complete"] is False

    def test_the_limit_of_one_task(self):
        cost = self.cost(5, by_task={"Задача A": 3.0, "Задача B": 1.0})
        view = budget_view(cost, {"budget_usd": 100, "budget_per_task_usd": 2.5}, [])
        assert view["over_limit"] == ["Задача A"]
        assert budget_view(cost, {"budget_usd": 100}, [])["over_limit"] == []

    def test_a_small_sum_is_shown_as_it_is(self):
        view = budget_view(self.cost(0.000325), {"budget_usd": 0.001}, [])
        assert "$0.000325" in view["story"] and "$0.001000" in view["story"]

    def test_the_state_is_the_one_the_engine_decides(self):
        from agent_teams import engine

        engine_rule = lambda spent, budget, ratio: (   # noqa: E731 (copied from op_budget)
            "limit" if spent >= round(budget, 6)
            else "warning" if spent >= round(ratio * budget, 6) else "ok")
        for spent in (0, 0.8, 2.6, 3.25, 5):
            for budget in (3.25, 10):
                assert budget_view(self.cost(spent), {"budget_usd": budget}, [])["state"] == \
                    engine_rule(spent, budget, 0.8), (spent, budget)

    def test_every_sum_says_what_it_leaves_out(self):
        cost = self.cost(3.0, unknown=2, by_task={"Задача A": 3.0})
        cost.update(unknown_by_task={"Задача A": 1, "Задача B": 1}, unknown_by_role={"AI-аналитик": 2})
        view = budget_view(cost, {}, [])
        # The stage that is priced and the one that is not are told apart.
        assert view["unknown_by_task"] == {"Задача A": 1, "Задача B": 1}
        assert view["unknown_by_role"] == {"AI-аналитик": 2}
        assert budget_view(self.cost(), {}, [])["unknown_by_task"] == {}


class TestModels:
    """The table of the advanced level: tokens and the price per model."""

    def record(self, model, provider="bothub", **fields):
        return {"model": model, "provider": provider, "input_tokens": 1000, "output_tokens": 400,
                "cache_read_tokens": 200, "cache_write_tokens": 50, "total_tokens": 1400,
                "cost_usd": None, "cost_quality": "unknown", **fields}

    def test_one_row_per_model_with_its_price(self):
        rows = model_rows([self.record("glm-5.3-flash"), self.record("glm-5.3-flash", output_tokens=600),
                           self.record("glm-5.3"), self.record(None, provider=None)])
        assert [(r["provider"], r["model"], r["records"]) for r in rows] == [
            ("bothub", "glm-5.3-flash", 2), ("bothub", "glm-5.3", 1), ("не указан", "не указана", 1)]
        flash, full, nameless = rows
        assert (flash["input_tokens"], flash["output_tokens"], flash["cache_read_tokens"],
                flash["cache_write_tokens"]) == (2000, 1000, 400, 100)
        # The longest matching name has its own price.
        assert flash["price_per_1m"] == {"input": 0.10, "output": 0.40, "cache_read": 0.02}
        assert full["price_per_1m"] == {"input": 0.60, "output": 2.20, "cache_read": 0.10}
        assert nameless["price_per_1m"] is None
        # Tokens are summed; money is the cost of each record, a record
        # without one is counted and never priced at zero.
        assert (flash["usd"], flash["unknown"], flash["quality"]) == (round((2000 * .1 + 1000 * .4 + 400 * .02) / 1e6, 6), 0, ["estimated"])
        assert (nameless["usd"], nameless["unknown"]) == (0.0, 1)

    def test_a_stored_price_is_kept_and_a_flash_model_is_not_priced_as_its_parent(self):
        priced = self.record("glm-5.3-flash", cost_usd=0.000123, cost_quality="stored")
        [row] = model_rows([priced])
        assert (row["usd"], row["quality"]) == (0.000123, ["stored"])
        [row] = model_rows([self.record("glm-5.3-flash")])
        assert row["usd"] == round((1000 * .10 + 400 * .40 + 200 * .02) / 1e6, 6)
        assert model_rows([]) == []


class TestRecovery:
    def test_no_problems(self):
        view = recovery_view([task("a", "completed")], {"a": [item(1, "task_claimed")]})
        assert view["total"] == 0 and view["story"].startswith("Проблем не было")
        assert view["kinds"] == [] and view["repeated"] == {}

    def test_a_lost_executor_that_came_back(self):
        events = {"a": [item(1, "task_claimed"), item(2, "agent_timeout", "Специалист перестал отвечать"),
                        item(3, "task_released"), item(4, "task_claimed")]}
        task_a = task("a", "completed", restarts=1, metadata={"quarantine_dir": "/q"})
        view = recovery_view([task_a], events)
        [incident] = view["incidents"]
        assert incident["kind"] == "executor" and incident["title"] == "Исполнитель перестал отвечать"
        assert incident["did"] == ["вернула задачу в очередь",
                                   "сохранила рабочую область исполнителя в архиве"]
        assert incident["outcome"] == "задача запущена заново"
        assert incident["saved"] == "рабочая область прежней попытки лежит в архиве"
        assert incident["human_needed"] is False and incident["risk_of_loss"] is False
        assert view["resolved"] == 1 and view["open"] == 0
        assert view["story"] == "Было проблем: 1. Ни одна не ждёт вашего решения."

    def test_a_check_that_failed_twice_and_stopped_the_task(self):
        events = {"b": [item(1, "task_claimed"), item(2, "task_submitted"),
                        item(3, "verification_failed", detail="Замечания: нет оценок"),
                        item(4, "task_submitted"), item(5, "verification_failed"),
                        item(6, "task_blocked")]}
        task_b = task("b", "blocked", restarts=0, verification_failures=2)
        view = recovery_view([task_b], events)
        kinds = sorted(i["kind"] for i in view["incidents"])
        assert kinds == ["blocked", "check", "check"]
        blocked = next(i for i in view["incidents"] if i["kind"] == "blocked")
        assert blocked["human_needed"] is True
        assert blocked["outcome"] == "нужна помощь: задача остановлена"
        assert blocked["action"].startswith("Разрешите ещё одну попытку")
        # What waits for the owner stands first, so the check that was
        # dealt with is found by its meaning, not by its place.
        first = next(i for i in view["incidents"]
                     if i["kind"] == "check" and not i["human_needed"])
        # The first failed check was answered by a rework: it is not an open problem.
        assert first["outcome"] == "исполнитель доработал результат и передал его снова"
        assert first["human_needed"] is False
        assert [i["human_needed"] for i in view["incidents"]] == [True, True, False]
        assert view["open"] == 2 and view["repeated"] == {"Задача B": 3}
        assert view["kinds"] == [{"kind": "check", "title": "Результат не прошёл проверку", "count": 2},
                                 {"kind": "blocked", "title": "Задача остановлена", "count": 1}]
        assert "Сейчас нужна ваша помощь по 1 задаче" in view["story"]
        check = next(i for i in view["incidents"] if i["kind"] == "check")
        assert "передала замечания проверяющего исполнителю" in check["did"]

    def test_the_block_recovery_writes_and_lifts_at_once_is_not_a_problem(self):
        events = {"a": [item(1, "task_claimed"), item(2, "agent_timeout"),
                        item(3, "task_blocked", "Исполнитель потерян (запись в журнале)"),
                        item(4, "task_released")]}
        view = recovery_view([task("a", "in_progress", restarts=1)], events)
        assert [i["kind"] for i in view["incidents"]] == ["executor"]

    def test_incidents_come_newest_first(self):
        events = {"a": [item(1, "verification_failed", ts="2026-11-25T09:01:00.000Z"),
                        item(2, "verification_failed", ts="2026-11-25T09:05:00.000Z")]}
        view = recovery_view([task("a", "in_progress")], events)
        assert [i["when"][11:16] for i in view["incidents"]] == ["09:05", "09:01"]

    def test_who_continued_the_work_is_named(self):
        events = {"a": [item(1, "task_claimed"), item(2, "agent_timeout"),
                        item(3, "task_released"), {**item(4, "task_claimed"), "specialist": "AI-аналитик 2"}]}
        [incident] = recovery_view([task("a", "in_progress", restarts=1)], events)["incidents"]
        assert incident["outcome"] == "задачу продолжил AI-аналитик 2"
        # Nothing was archived for this attempt, so nothing is claimed to be.
        assert incident["saved"] is None
        # Without a name on the claim the work is still said to go on.
        events["a"][3].pop("specialist")
        [incident] = recovery_view([task("a", "in_progress", restarts=1)], events)["incidents"]
        assert incident["outcome"] == "задача запущена заново"

    def test_a_spent_budget_is_a_problem_that_waits_for_the_owner(self):
        events = {"a": [item(1, "verification_failed", ts="2026-11-25T09:01:00.000Z")]}
        tasks = [task("a", "completed", submitted_at="x")]
        spent = {"state": "limit"}
        view = recovery_view(tasks, events, spent)
        budget = view["incidents"][0]           # what waits for the owner stands first
        assert budget["kind"] == "budget" and budget["title"] == "Бюджет исчерпан"
        assert budget["human_needed"] is True and budget["risk_of_loss"] is False
        assert budget["task"] is None and budget["when"] is None
        assert budget["action"].startswith("Увеличьте бюджет")
        assert view["kinds"][0] == {"kind": "budget", "title": "Бюджет исчерпан", "count": 1}
        assert "Сейчас нужна ваша помощь по бюджету." in view["story"]
        # With an open task the owner is asked about both.
        events["a"].append(item(2, "task_blocked"))
        tasks = [task("a", "blocked", verification_failures=2)]
        story = recovery_view(tasks, events, spent)["story"]
        assert "Сейчас нужна ваша помощь по 1 задаче и бюджету." in story
        # A budget that still has room is no problem.
        assert recovery_view(tasks, events, {"state": "warning"})["kinds"][0]["kind"] == "check"
        assert recovery_view(tasks, events)["incidents"][-1]["kind"] == "blocked"


class TestIntegration:
    def run(self, status="active"):
        return {"status": status}

    def test_collecting(self):
        tasks = [task("a", "completed"), task("b", "in_progress"), task("c", "pending")]
        view = integration_view(tasks, self.run(), [])
        assert view["state"] == "collecting"
        assert view["story"] == "Принято 1 из 3 результатов. Объединение начнётся, когда будут приняты все."
        assert [s["text"] for s in view["steps"]] == ["1 из 3", "ещё нет", "ещё нет"]
        assert [s["done"] for s in view["steps"]] == [False, False, False]

    def test_all_accepted_but_not_integrated(self):
        view = integration_view([task("a", "completed")], self.run(), [])
        assert view["story"] == "Все результаты приняты. Можно запускать объединение."
        assert [s["done"] for s in view["steps"]] == [True, False, False]

    def test_integrated_then_applied(self):
        tasks = [task("a", "completed")]
        ready = integration_view(tasks, self.run("apply_pending"), [])
        assert ready["state"] == "ready" and [s["done"] for s in ready["steps"]] == [True, True, False]
        assert "Осталось подтвердить" in ready["story"]
        applied = integration_view(tasks, self.run("finished"), [])
        assert applied["state"] == "applied" and all(s["done"] for s in applied["steps"])
        assert applied["story"] == "Финальная версия готова: изменения внесены в проект."

    def test_a_conflict_is_shown_with_what_came_of_it(self):
        events = [item(1, "task_claimed"),
                  item(2, "workspace_conflict", "Не удалось объединить результаты", "Исходные версии сохранены.")]
        view = integration_view([task("a", "completed")], self.run("integration_failed"), events)
        assert view["state"] == "conflict"
        assert view["conflict"]["title"] == "Не удалось объединить результаты"
        assert "Исходные версии сохранены" in view["story"]
        assert "запустить объединение заново" in view["story"]

    def test_no_tasks(self):
        assert integration_view([], self.run(), [])["state"] == "empty"

    def test_a_cancelled_task_does_not_count(self):
        tasks = [task("a", "completed"), task("b", "cancelled")]
        assert integration_view(tasks, self.run(), [])["story"] == \
            "Все результаты приняты. Можно запускать объединение."
