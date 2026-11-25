"""Comparing teams that ran one set of tasks (v2.1 spec 19). A sentence
about speed or money needs two finished teams of the same declared
benchmark; nothing else may produce one."""
from __future__ import annotations

from agent_teams.analytics import benchmark_compare


def row(team, executors, *, finished=True, duration=3600, spent=1.0, complete=True,
        first_time=3, judged=4):
    return {"team": team, "executors": executors, "finished": finished, "duration_s": duration,
            "spent_usd": spent, "cost_complete": complete, "accepted": 4, "total": 4,
            "first_time": first_time, "judged": judged}


class TestBenchmark:
    def test_the_example_of_the_spec(self):
        teams = [row("one", 1, duration=4000, spent=10.0), row("three", 3, duration=3000, spent=11.2)]
        view = benchmark_compare("billing", teams, "three")
        assert view["statement"] == ("Команда из 3 специалистов выполнила набор задач на 25% быстрее, "
                                     "чем команда из 1 исполнителя, при этом затраты увеличились на 12%. "
                                     "С первого раза прошло 3 из 4 результатов против 3 из 4.")

    def test_slower_cheaper_and_unchanged(self):
        slow = benchmark_compare("b", [row("one", 1, duration=3000), row("two", 2, duration=3600, spent=0.9)],
                                 "two")["statement"]
        assert "на 20% медленнее" in slow and "затраты снизились на 10%" in slow
        same = benchmark_compare("b", [row("one", 1, duration=3000, spent=5), row("two", 2, duration=3030, spent=5.05)],
                                 "two")["statement"]
        assert "за то же время" in same and "затраты не изменились" in same

    def test_money_is_not_compared_when_a_price_is_unknown(self):
        teams = [row("one", 1), row("two", 2, complete=False)]
        statement = benchmark_compare("b", teams, "two")["statement"]
        assert "затраты не сравнить: цена части записей неизвестна" in statement

    def test_no_sentence_without_two_finished_teams(self):
        one = benchmark_compare("b", [row("one", 1)], "one")
        assert one["statement"] is None and "не меньше двух команд" in one["note"]
        unfinished = benchmark_compare("b", [row("one", 1), row("two", 2, finished=False)], "two")
        assert unfinished["statement"] is None and "не меньше двух команд" in unfinished["note"]
        assert benchmark_compare("b", [], "x")["note"] == "Ни одна команда этого набора пока не записана."

    def test_the_smallest_team_is_the_baseline_and_gets_no_sentence_about_itself(self):
        view = benchmark_compare("b", [row("one", 1), row("five", 5)], "one")
        assert view["statement"] is None and "с ней сравнивают остальные" in view["note"]
        assert [r["team"] for r in view["rows"]] == ["one", "five"]

    def test_a_team_without_a_declared_size_is_left_out(self):
        teams = [row("one", 1), {"team": "odd", "executors": None, "finished": True}]
        view = benchmark_compare("b", teams, "one")
        assert [r["team"] for r in view["rows"]] == ["one"]
