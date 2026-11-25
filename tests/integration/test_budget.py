"""The budget of a team (v2.1 spec 11.2): agreed in the config, watched by
the engine, and enforced where work starts.

The rule under test: once the agreed budget is spent, no new attempt
begins; an attempt that is already running is never stopped; the lead
can raise the budget, and every change is in the journal. A cost the
engine does not know is not a zero and does not count.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import BudgetExceeded, PermissionDenied, UsageError

SCRIPTS = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts"


@pytest.fixture
def team(tmp_path):
    (tmp_path / "out").mkdir()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"tasks": [
        {"id": "a", "title": "A", "assignee": "w1", "files_write": ["out/a.md"],
         "acceptance": ["done"], "deps": []},
        {"id": "b", "title": "B", "assignee": "w2", "files_write": ["out/b.md"],
         "acceptance": ["done"], "deps": []},
        {"id": "c", "title": "C", "assignee": "w1", "files_write": ["out/c.md"],
         "acceptance": ["done"], "deps": []}]}), encoding="utf-8")
    created = engine.op_init(tmp_path, "t", spec)
    return {"root": tmp_path, "lead": created["lead_token"]}


def budget_events(root) -> list[dict]:
    """The budget decisions as the journal holds them."""
    with sqlite3.connect(f"file:{root / '.agent-teams' / 't' / 'state.db'}?mode=ro",
                         uri=True) as conn:
        rows = conn.execute("SELECT metadata_json FROM events WHERE event_type = 'budget_changed'"
                            " ORDER BY seq").fetchall()
    return [json.loads(row[0]) for row in rows]


def spend(root, task_id="a", tokens=1000, agent="w1", model="glm-5.3-flash"):
    """One usage record worth a known price (a flash model)."""
    engine.op_tokens(root, "t", task_id, tokens, agent, model)


ONE_RECORD_USD = 1000 * (0.25 * 0.10 + 0.75 * 0.40) / 1_000_000    # = 0.000325


class TestShowingAndSetting:
    def test_no_budget_is_the_default(self, team):
        shown = engine.op_budget(team["root"], "t")
        assert (shown["budget_usd"], shown["budget_per_task_usd"], shown["spent_usd"]) == \
            (None, None, 0.0)
        assert shown["remaining_usd"] is None and shown["tasks"] == {}
        assert shown["state"] == "none" and shown["tasks_at_limit"] == []

    def test_the_lead_sets_a_budget_and_it_is_in_the_journal(self, team):
        set_ = engine.op_budget(team["root"], "t", team["lead"], usd=10, per_task=2,
                                warn_ratio=0.9)
        assert (set_["budget_usd"], set_["budget_per_task_usd"], set_["budget_warn_ratio"]) == \
            (10, 2, 0.9)
        assert budget_events(team["root"]) == [{"budget_usd": 10, "budget_per_task_usd": 2,
                                               "budget_warn_ratio": 0.9}]
        # A value not mentioned keeps its own; 'none' removes a limit.
        kept = engine.op_budget(team["root"], "t", team["lead"], usd=None)
        assert (kept["budget_usd"], kept["budget_per_task_usd"], kept["budget_warn_ratio"]) == \
            (None, 2, 0.9)

    def test_only_the_lead_changes_it(self, team):
        with pytest.raises(PermissionDenied):
            engine.op_budget(team["root"], "t", None, usd=10)
        with pytest.raises(PermissionDenied):
            engine.op_budget(team["root"], "t", "forged", usd=10)
        assert engine.op_budget(team["root"], "t", "forged")["budget_usd"] is None   # showing is free

    @pytest.mark.parametrize("change", [{"usd": 0}, {"usd": -1}, {"usd": "10"},
                                        {"warn_ratio": 1.5}, {"per_task": -2}])
    def test_a_bad_value_changes_nothing(self, team, change):
        with pytest.raises(UsageError):
            engine.op_budget(team["root"], "t", team["lead"], **change)
        assert engine.op_budget(team["root"], "t")["budget_usd"] is None
        assert budget_events(team["root"]) == []

    def test_the_spend_and_what_is_left(self, team):
        engine.op_budget(team["root"], "t", team["lead"], usd=0.001, per_task=0.0005)
        spend(team["root"], "a")
        spend(team["root"], "b", agent="w2")
        spend(team["root"], "c", model="mystery-9")          # no price: not a zero
        shown = engine.op_budget(team["root"], "t")
        assert shown["spent_usd"] == pytest.approx(2 * ONE_RECORD_USD)
        assert shown["remaining_usd"] == pytest.approx(0.001 - 2 * ONE_RECORD_USD)
        assert shown["unknown_records"] == 1
        assert shown["tasks"] == {"a": pytest.approx(ONE_RECORD_USD),
                                  "b": pytest.approx(ONE_RECORD_USD), "c": 0.0}
        assert shown["state"] == "ok"               # 0.00065 of 0.001
        assert shown["remaining_usd"] == pytest.approx(0.00035)


class TestTheHardLimit:
    def budget(self, team, **limits):
        engine.op_budget(team["root"], "t", team["lead"], **limits)

    def test_no_new_attempt_starts_once_the_budget_is_spent(self, team):
        self.budget(team, usd=1.5 * ONE_RECORD_USD)           # one record fits, two do not
        spend(team["root"], "a")
        claim = engine.op_claim(team["root"], "t", "a", "w1")   # one record: still inside
        assert claim["task"] == "a"
        spend(team["root"], "a")
        with pytest.raises(BudgetExceeded, match="BUDGET_LIMIT"):
            engine.op_claim(team["root"], "t", "b", "w2")

    def test_a_budget_that_is_exactly_spent_is_spent(self, team):
        # 0.00065 is not 0.0006500000000000001 in floating point; for money it is.
        self.budget(team, usd=2 * ONE_RECORD_USD)
        spend(team["root"], "a")
        spend(team["root"], "a")
        with pytest.raises(BudgetExceeded, match=r"0\.000650 of the \$0\.000650 budget"):
            engine.op_claim(team["root"], "t", "b", "w2")

    def test_the_attempt_that_runs_is_not_stopped(self, team):
        self.budget(team, usd=1.5 * ONE_RECORD_USD)
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        spend(team["root"], "a")
        spend(team["root"], "a")                               # the limit is passed now
        assert engine.op_heartbeat(team["root"], "t", "w1", claim["execution_token"])["task"] == "a"
        assert engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])["task"] == "a"
        assert engine.op_status(team["root"], "t")["tasks"][0]["status"] == "awaiting_verification"

    def test_a_refused_claim_leaves_nothing_behind(self, team):
        self.budget(team, usd=ONE_RECORD_USD)
        spend(team["root"], "a")
        before = len(engine.op_logs(team["root"], "t", 100))
        with pytest.raises(BudgetExceeded):
            engine.op_claim(team["root"], "t", "b", "w2")
        task = engine.op_inspect(team["root"], "t", "b")["task"]
        assert task["status"] == "pending" and task["attempt"] == 0
        assert len(engine.op_logs(team["root"], "t", 100)) == before
        assert [w["task"] for w in engine.op_workspace(team["root"], "t")["workspaces"]
                if w["exists"]] == []

    def test_raising_the_budget_lets_the_work_go_on(self, team):
        self.budget(team, usd=ONE_RECORD_USD)
        spend(team["root"], "a")
        with pytest.raises(BudgetExceeded):
            engine.op_claim(team["root"], "t", "b", "w2")
        self.budget(team, usd=5)
        assert engine.op_claim(team["root"], "t", "b", "w2")["task"] == "b"

    def test_the_limit_of_one_task(self, team):
        self.budget(team, per_task=ONE_RECORD_USD)
        spend(team["root"], "a")
        with pytest.raises(BudgetExceeded, match="task a spent its"):
            engine.op_claim(team["root"], "t", "a", "w1")
        assert engine.op_claim(team["root"], "t", "c", "w1")["task"] == "c"   # another task is free

    def test_the_three_states_of_a_budget(self, team):
        engine.op_budget(team["root"], "t", team["lead"], usd=10 * ONE_RECORD_USD, warn_ratio=0.8)
        assert engine.op_budget(team["root"], "t")["state"] == "ok"
        for _ in range(7):
            spend(team["root"], "a")
        assert engine.op_budget(team["root"], "t")["state"] == "ok"        # 7 of 10
        spend(team["root"], "a")
        assert engine.op_budget(team["root"], "t")["state"] == "warning"   # 8 of 10: 80%
        assert "warning: the budget is nearly spent" in self.shown(team)
        for _ in range(2):
            spend(team["root"], "a")
        assert engine.op_budget(team["root"], "t")["state"] == "limit"     # 10 of 10

    @staticmethod
    def shown(team) -> str:
        from agent_teams import cli

        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["budget", "--team", "t", "--project-root", str(team["root"])])
        return out.getvalue()

    def test_a_cost_the_engine_does_not_know_does_not_count(self, team):
        self.budget(team, usd=ONE_RECORD_USD)
        spend(team["root"], "a", model="mystery-9")
        spend(team["root"], "a", model="mystery-9")
        assert engine.op_claim(team["root"], "t", "b", "w2")["task"] == "b"

    def test_through_the_command_line_it_is_an_error_not_a_traceback(self, team):
        self.budget(team, usd=ONE_RECORD_USD)
        spend(team["root"], "a")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "team.py"), "claim", "--team", "t", "--task", "b",
             "--by", "w2", "--project-root", str(team["root"])],
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2
        assert "BUDGET_LIMIT" in proc.stderr and "Traceback" not in proc.stderr
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "team.py"), "budget", "--team", "t",
             "--project-root", str(team["root"]), "--json"],
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0
        shown = json.loads(proc.stdout)
        assert shown["budget_usd"] == pytest.approx(ONE_RECORD_USD)
        assert shown["spent_usd"] == pytest.approx(ONE_RECORD_USD)

    def test_the_command_line_says_it_as_a_person_reads_it(self, team, capsys):
        from agent_teams import cli

        engine.op_budget(team["root"], "t", team["lead"], usd=ONE_RECORD_USD)
        spend(team["root"], "a")
        spend(team["root"], "a", model="mystery-9")
        assert cli.main(["budget", "--team", "t", "--project-root", str(team["root"])]) == 0
        out = capsys.readouterr().out
        assert "LIMIT REACHED" in out and "running executors are not stopped" in out
        assert "1 usage records carry no price" in out
        assert "$0.000325" in out                 # a small sum is shown as it is

    def test_a_bad_limit_on_the_command_line(self, tmp_path, capsys):
        from agent_teams import cli

        (tmp_path / "out").mkdir()
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"tasks": [
            {"id": "a", "title": "A", "assignee": "w1", "files_write": ["out/a.md"],
             "acceptance": ["done"], "deps": []}]}), encoding="utf-8")
        engine.op_init(tmp_path, "t", spec)
        with pytest.raises(UsageError, match="must be a number or 'none'"):
            cli._cmd_budget(type("Args", (), {"project_root": str(tmp_path), "team": "t",
                                              "lead_token": "", "usd": "ten", "per_task": None,
                                              "warn_ratio": None, "json": False})())


class TestABenchmarkDeclared:
    def test_a_team_may_say_which_benchmark_it_runs(self, tmp_path):
        (tmp_path / "out").mkdir()
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({
            "config": {"benchmark": {"name": "billing-review", "executors": 3}},
            "tasks": [{"id": "a", "title": "A", "assignee": "w1", "files_write": ["out/a.md"],
                       "acceptance": ["done"], "deps": []}]}), encoding="utf-8")
        created = engine.op_init(tmp_path, "t", spec)
        assert created["tasks"] == 1
        assert engine.op_status(tmp_path, "t")["team"] == "t"
