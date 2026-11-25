"""The shapes the board draws (v2.1 spec 6 and 7): the plan graph and
the timeline, as pure functions over what the engine recorded."""
from __future__ import annotations

import pytest

from agent_teams.planview import SPAN_TEXT, plan_layout, timeline


def plan(*specs: tuple[str, list[str]]) -> list[dict]:
    """(id, deps) pairs in the order they have in the plan."""
    return [{"id": task_id, "title": task_id.upper(), "deps": deps, "state": "В очереди",
             "kind": "idle"} for task_id, deps in specs]


def place(layout: dict, task_id: str) -> tuple[int, int]:
    node = next(n for n in layout["nodes"] if n["id"] == task_id)
    return node["col"], node["row"]


class TestThePlanGraph:
    def test_a_chain_runs_left_to_right(self):
        layout = plan_layout(plan(("a", []), ("b", ["a"]), ("c", ["b"])))
        assert [place(layout, t) for t in "abc"] == [(0, 0), (1, 0), (2, 0)]
        assert layout["edges"] == [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        assert (layout["columns"], layout["rows"]) == (3, 1)

    def test_independent_tasks_share_a_column(self):
        layout = plan_layout(plan(("a", []), ("b", []), ("c", [])))
        assert [place(layout, t) for t in "abc"] == [(0, 0), (0, 1), (0, 2)]
        assert layout["edges"] == [] and (layout["columns"], layout["rows"]) == (1, 3)

    def test_a_fork_and_a_join(self):
        layout = plan_layout(plan(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])))
        assert place(layout, "a") == (0, 0)
        assert sorted(place(layout, t)[1] for t in "bc") == [0, 1]
        assert all(place(layout, t)[0] == 1 for t in "bc")
        assert place(layout, "d") == (2, 0)            # alone in its column
        assert (layout["columns"], layout["rows"]) == (3, 2)

    def test_rows_are_chosen_so_the_lines_do_not_cross(self):
        # The plan lists x (depends on b) before y (depends on a).
        layout = plan_layout(plan(("a", []), ("b", []), ("x", ["b"]), ("y", ["a"])))
        assert (place(layout, "a")[1], place(layout, "y")[1]) == (0, 0)
        assert (place(layout, "b")[1], place(layout, "x")[1]) == (1, 1)

    def test_a_task_stands_after_the_longest_chain_before_it(self):
        layout = plan_layout(plan(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])))
        assert place(layout, "d")[0] == 2             # not 1, though c is one step from a

    def test_a_dependency_the_engine_does_not_know_is_not_drawn(self):
        layout = plan_layout(plan(("a", []), ("b", ["ghost", "a"])))
        assert layout["edges"] == [{"from": "a", "to": "b"}]
        assert place(layout, "b") == (1, 0)

    def test_a_cycle_loses_nothing(self):
        # The engine rejects cycles at init; should one arrive all the
        # same, both tasks are still on the graph.
        layout = plan_layout(plan(("a", ["b"]), ("b", ["a"])))
        assert sorted(layout["nodes"], key=lambda n: n["id"]) == [
            {"id": "a", "col": 1, "row": 0}, {"id": "b", "col": 1, "row": 1}]

    def test_no_tasks(self):
        assert plan_layout([]) == {"nodes": [], "edges": [], "columns": 0, "rows": 0}

    def test_the_same_plan_lays_out_the_same_way(self):
        tasks = plan(("a", []), ("b", []), ("c", []), ("d", ["a", "b"]), ("e", ["b", "c"]),
                     ("f", ["d", "e"]), ("g", ["c"]), ("h", ["f", "g"]))
        first = plan_layout(tasks)
        assert plan_layout([dict(t) for t in tasks]) == first
        assert first["columns"] == 4 and first["rows"] == 3

    def test_a_hundred_tasks(self):
        # One first task, then nine independent chains of nine hanging off it.
        specs = [("t000", [])]
        for i in range(1, 100):
            specs.append((f"t{i:03d}", ["t000"] if i % 10 == 0 else [f"t{i - 1:03d}"]))
        layout = plan_layout(plan(*specs))
        assert len(layout["nodes"]) == 100 and len(layout["edges"]) == 99
        assert max(n["col"] for n in layout["nodes"]) == 10
        for edge in layout["edges"]:
            assert place(layout, edge["from"])[0] < place(layout, edge["to"])[0]

    def test_nodes_come_in_the_order_of_the_plan(self):
        layout = plan_layout(plan(("z", []), ("a", ["z"]), ("m", ["z"])))
        assert [n["id"] for n in layout["nodes"]] == ["z", "a", "m"]


def item(seq: int, event_type: str, minute: int, title: str, kind: str = "busy") -> dict:
    return {"seq": seq, "ts": f"2026-11-25T09:{minute:02d}:00.000Z", "title": title,
            "type": event_type, "kind": kind, "icon": "circle"}


class TestTheTimeline:
    TASKS = [{"id": "a", "title": "Отчёт", "state": "Принято", "kind": "done"},
             {"id": "b", "title": "Очередь", "state": "В очереди", "kind": "idle"}]

    def test_a_task_with_a_failed_check_and_a_fix(self):
        events = {"a": [
            item(1, "task_claimed", 5, "Приступил к задаче"),
            item(2, "task_submitted", 15, "Передал результат на проверку", "busy"),
            item(3, "verification_failed", 18, "Проверка не пройдена", "problem"),
            item(4, "task_submitted", 27, "Передал результат на проверку", "busy"),
            item(5, "verification_passed", 35, "Проверка пройдена", "done"),
            item(6, "task_completed", 35, "Этап завершён", "done"),
        ]}
        got = timeline(self.TASKS, events, events["a"][0]["ts"], "2026-11-25T09:40:00.000Z")
        [report, queue] = got["rows"]
        assert [(s["kind"], s["from"][11:16], s["to"][11:16]) for s in report["spans"]] == [
            ("work", "09:05", "09:15"), ("check", "09:15", "09:18"),
            ("fix", "09:18", "09:27"), ("check", "09:27", "09:35"), ("done", "09:35", "09:40")]
        assert all(s["text"] == SPAN_TEXT[s["kind"]] for s in report["spans"])
        assert [(m["title"], m["kind"]) for m in report["marks"]] == [
            ("Приступил к задаче", "busy"), ("Передал результат на проверку", "busy"),
            ("Проверка не пройдена", "problem"), ("Передал результат на проверку", "busy"),
            ("Проверка пройдена", "done"), ("Этап завершён", "done")]
        assert queue["spans"] == [] and queue["marks"] == []   # nothing was recorded

    def test_the_last_span_runs_to_now(self):
        events = {"a": [item(1, "task_claimed", 5, "Приступил к задаче")]}
        got = timeline(self.TASKS, events, "2026-11-25T09:00:00.000Z", "2026-11-25T09:50:00.000Z")
        assert got["rows"][0]["spans"] == [
            {"from": "2026-11-25T09:05:00.000Z", "to": "2026-11-25T09:50:00.000Z",
             "kind": "work", "text": "работа"}]

    def test_a_bookkeeping_line_is_a_mark_and_not_a_span(self):
        events = {"a": [item(1, "task_claimed", 5, "Приступил к задаче"),
                        item(2, "workspace_created", 5, "Подготовлена рабочая область"),
                        item(3, "task_submitted", 9, "Передал результат на проверку", "busy")]}
        got = timeline(self.TASKS, events, "2026-11-25T09:00:00.000Z", "2026-11-25T09:10:00.000Z")
        assert [s["kind"] for s in got["rows"][0]["spans"]] == ["work", "check"]
        assert len(got["rows"][0]["marks"]) == 3

    def test_recovery_after_a_failure(self):
        events = {"a": [item(1, "task_claimed", 5, "Приступил к задаче"),
                        item(2, "agent_timeout", 20, "Специалист перестал отвечать", "problem"),
                        item(3, "task_released", 20, "Задача возвращена в очередь", "normal"),
                        item(4, "task_claimed", 25, "Приступил к задаче")]}
        got = timeline(self.TASKS, events, "s", "2026-11-25T09:30:00.000Z")
        assert [s["kind"] for s in got["rows"][0]["spans"]] == ["work", "recovery", "wait", "work"]

    def test_the_frame_is_passed_through(self):
        got = timeline([], {}, "2026-11-25T09:00:00.000Z", "2026-11-25T10:00:00.000Z")
        assert (got["start"], got["end"], got["rows"]) == ("2026-11-25T09:00:00.000Z",
                                                           "2026-11-25T10:00:00.000Z", [])
