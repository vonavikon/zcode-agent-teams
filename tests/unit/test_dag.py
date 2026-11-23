"""DAG checks: cycles, order, ancestry."""
from __future__ import annotations

from agent_teams.validation import find_cycle, is_ancestor, topological_order


def task(tid, deps=()):
    return {"id": tid, "deps": list(deps)}


class TestCycles:
    def test_acyclic(self):
        assert find_cycle([task("a"), task("b", ["a"])]) is None

    def test_self_cycle(self):
        cycle = find_cycle([task("a", ["a"])])
        assert cycle == ["a", "a"]

    def test_two_cycle(self):
        cycle = find_cycle([task("a", ["b"]), task("b", ["a"])])
        assert cycle in (["a", "b", "a"], ["b", "a", "b"])

    def test_long_cycle(self):
        tasks = [task("a", ["c"]), task("b", ["a"]), task("c", ["b"])]
        cycle = find_cycle(tasks)
        assert cycle is not None and len(cycle) == 4
        assert cycle[0] == cycle[-1]

    def test_all_tasks_checked(self):
        # The first two tasks are fine; the cycle sits later.
        tasks = [task("ok1"), task("ok2", ["ok1"]),
                 task("x", ["y"]), task("y", ["x"])]
        assert find_cycle(tasks) is not None


class TestTopologicalOrder:
    def test_linear(self):
        assert topological_order([task("b", ["a"]), task("a")]) == ["a", "b"]

    def test_diamond(self):
        tasks = [task("d", ["b", "c"]), task("b", ["a"]), task("c", ["a"]), task("a")]
        order = topological_order(tasks)
        assert order.index("a") < order.index("b") < order.index("d")
        assert order.index("a") < order.index("c") < order.index("d")

    def test_independent_sorted(self):
        assert topological_order([task("c"), task("a"), task("b")]) == ["a", "b", "c"]

    def test_cycle_gives_none(self):
        assert topological_order([task("a", ["b"]), task("b", ["a"])]) is None


class TestAncestry:
    TASKS = [task("app", ["lib"]), task("lib", ["core"]), task("doc", [])]

    def test_direct(self):
        assert is_ancestor(self.TASKS, "lib", "app") is True

    def test_transitive(self):
        assert is_ancestor(self.TASKS, "core", "app") is True

    def test_reverse_direction(self):
        assert is_ancestor(self.TASKS, "app", "core") is False

    def test_unrelated(self):
        assert is_ancestor(self.TASKS, "doc", "app") is False
