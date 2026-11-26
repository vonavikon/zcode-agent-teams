"""Cost of AI usage (spec 11.2): the price of the right model, stored once.

Two defects were found here on 2026-11-25 while building the dashboard:

- "glm-5.3-flash" was priced as "glm-5.3": the price table was searched
  by substring in dictionary order, and the shorter name comes first.
  A flash model cost 6 times too much on input.
- No cost was ever stored (the column stayed NULL), and the manual
  ``tokens`` command bypassed the one writer there was. Every reader
  recomputed from the current price table, so a price change rewrote
  the history.

These tests pin both: the longest matching name wins, and the cost of
the moment the work was done is what every reader reports.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.analytics import budget_view
from agent_teams.observability import (PRICE_TABLE_USD_1M, collect_metrics, compute_cost,
                                       cost_of)
from agent_teams.storage import open_store
from agent_teams.viewmodel import cost_view

MILLION = 1_000_000
# A record with a total only (no input/output split) is estimated as 1:3.
FLASH_TOTAL = 0.25 * 0.10 + 0.75 * 0.40          # per million tokens
GLM_TOTAL = 0.25 * 0.60 + 0.75 * 2.20


@pytest.fixture
def project(tmp_path):
    (tmp_path / "out").mkdir()
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps({"tasks": [
        {"id": "a", "title": "A", "assignee": "w1", "files_write": ["out/a.md"],
         "acceptance": ["done"], "deps": []}]}), encoding="utf-8")
    engine.op_init(tmp_path, "t", spec_file)
    return tmp_path


def flash(root: Path, tokens: int = MILLION, model: str = "glm-5.3-flash") -> None:
    engine.op_tokens(root, "t", "a", tokens, "w1", model)


def usage_rows(root: Path) -> list[dict]:
    with open_store(root / ".agent-teams" / "t") as store:
        return store.list_usage("t")


class TestTheRightModelHasTheRightPrice:
    @pytest.mark.parametrize("model, input_usd", [
        ("glm-5.3-flash", 0.10), ("glm-5.3", 0.60), ("GLM-5.3-FLASH", 0.10),
        ("glm-5.3-flash", 0.10), ("glm-5.4", 0.60), ("glm-5.3-flashx", 0.10),
    ])
    def test_the_longest_matching_name_wins(self, model, input_usd):
        cost, quality = compute_cost({"model": model, "input_tokens": MILLION,
                                      "output_tokens": 0})
        assert cost == pytest.approx(input_usd)
        assert quality == "estimated"

    def test_tokens_are_priced_by_kind(self):
        cost, _quality = compute_cost({"model": "gpt-5.2", "input_tokens": MILLION,
                                       "output_tokens": MILLION,
                                       "cache_read_tokens": MILLION})
        assert cost == pytest.approx(1.25 + 10.00 + 0.125)

    def test_a_total_without_a_split_is_an_explicit_estimate(self):
        cost, quality = compute_cost({"model": "glm-5.3", "total_tokens": MILLION})
        assert cost == pytest.approx(0.25 * 0.60 + 0.75 * 2.20)
        assert quality == "estimated"

    @pytest.mark.parametrize("record", [
        {"total_tokens": 5}, {"model": "mystery-9", "total_tokens": 5},
        {"model": "glm-5.3"}, {"model": "glm-5.3", "total_tokens": 0},
    ])
    def test_no_price_or_no_numbers_is_unknown_never_free(self, record):
        assert compute_cost(record) == (None, "unknown")


class TestTheCostIsStoredOnce:
    def test_a_manual_record_stores_its_cost(self, project):
        flash(project)
        [row] = usage_rows(project)
        assert row["cost_usd"] == pytest.approx(FLASH_TOTAL)
        assert row["cost_quality"] == "estimated"

    def test_a_notification_record_stores_its_cost(self, project, tmp_path):
        notification = tmp_path / "notif.txt"
        notification.write_text(
            "<usage>\nsubagent_tokens: 1000000\ntool_uses: 3\nduration_ms: 900\n</usage>",
            encoding="utf-8")
        engine.op_usage(project, "t", "a", "w1", notification)
        [row] = usage_rows(project)
        # A notification carries no model: the price is unknown, and stored so.
        assert (row["cost_usd"], row["cost_quality"]) == (None, "unknown")

    def test_the_engine_reports_the_flash_price_not_the_parent_price(self, project):
        flash(project, tokens=2 * MILLION)
        assert engine.op_cost(project, "t")["total_usd"] == pytest.approx(2 * FLASH_TOTAL)
        assert engine.op_cost(project, "t")["per_task"]["a"]["cost_usd"] == \
            pytest.approx(2 * FLASH_TOTAL)

    def test_a_price_change_does_not_rewrite_the_history(self, project, monkeypatch):
        flash(project)
        monkeypatch.setitem(PRICE_TABLE_USD_1M, "glm-5.3-flash", (99.0, 99.0, 99.0))
        [row] = usage_rows(project)
        with monkeypatch.context() as cheaper:
            cheaper.setitem(PRICE_TABLE_USD_1M, "glm-5.3-flash", (99.0, 99.0, 99.0))
            assert compute_cost(row)[0] == pytest.approx(0.25 * 99.0 + 0.75 * 99.0)
        assert cost_of(row)[0] == pytest.approx(FLASH_TOTAL)   # the history has not
        assert engine.op_cost(project, "t")["total_usd"] == pytest.approx(FLASH_TOTAL)
        assert cost_view(usage_rows(project))["usd"] == pytest.approx(FLASH_TOTAL)
        with open_store(project / ".agent-teams" / "t") as store:
            metrics = collect_metrics(store, "t")
        assert metrics["cost_total_usd"] == pytest.approx(FLASH_TOTAL)
        out = project / "report.html"
        engine.op_report(project, "t", out)
        # The report says what the cost says, in the words of the board.
        said = budget_view(cost_view(usage_rows(project)), {}, [])["spent_text"]
        assert said and said in out.read_text(encoding="utf-8")

    def test_a_record_from_before_costs_were_stored_is_estimated_from_the_table(
            self, project):
        engine.op_tokens(project, "t", "a", MILLION, "w1", "glm-5.3")
        with open_store(project / ".agent-teams" / "t") as store:
            with store.transaction():
                store.run("UPDATE token_usage SET cost_usd = NULL, cost_quality = 'unknown'")
        [row] = usage_rows(project)
        assert row["cost_usd"] is None
        assert cost_of(row) == (pytest.approx(GLM_TOTAL), "estimated")
        assert engine.op_cost(project, "t")["total_usd"] == pytest.approx(GLM_TOTAL)

    def test_an_unknown_cost_stays_unknown_for_every_reader(self, project):
        engine.op_tokens(project, "t", "a", MILLION, "w1", "mystery-9")
        assert engine.op_cost(project, "t") == {
            "per_task": {"a": {"tokens": MILLION, "cost_usd": 0.0}},
            "total_usd": 0.0, "quality": "unknown"}
        view = cost_view(usage_rows(project))
        assert (view["usd"], view["unknown_records"], view["complete"]) == (0.0, 1, False)


class TestTheDashboardAndTheEngineAgree:
    @pytest.mark.parametrize("model, tokens", [
        ("glm-5.3-flash", MILLION), ("glm-5.3", 3 * MILLION), ("gpt-5.2", 500_000),
    ])
    def test_both_report_the_same_number(self, project, model, tokens):
        engine.op_tokens(project, "t", "a", tokens, "w1", model)
        assert cost_view(usage_rows(project))["usd"] == \
            pytest.approx(engine.op_cost(project, "t")["total_usd"])

    def test_every_row_has_a_cost_decision_stored(self, project, tmp_path):
        notification = tmp_path / "notif.txt"
        notification.write_text("<usage>\nsubagent_tokens: 10\n</usage>", encoding="utf-8")
        engine.op_tokens(project, "t", "a", MILLION, "w1", "glm-5.3-flash")
        engine.op_usage(project, "t", "a", "w1", notification)
        stored = [(row["cost_usd"], row["cost_quality"]) for row in usage_rows(project)]
        assert stored == [(pytest.approx(FLASH_TOTAL), "estimated"), (None, "unknown")]
