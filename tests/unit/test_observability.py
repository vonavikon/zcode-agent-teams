"""Observability: usage adapters, cost, metrics (spec section 9)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import UsageError
from agent_teams.observability import (collect_metrics, compute_cost,
                                       parse_notification)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "out").mkdir()
    spec = {"tasks": [
        {"id": "a", "title": "A", "assignee": "w1", "files_write": ["out/a.md"],
         "acceptance": ["done"], "deps": []},
        {"id": "b", "title": "B", "assignee": "w2", "files_write": ["out/b.md"],
         "acceptance": ["done"], "deps": []}]}
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    created = engine.op_init(tmp_path, "t", spec_file)
    return tmp_path, created["lead_token"]


NOTIF_COLON = """agentId: x (use SendMessage with to: 'x')
<usage>subagent_tokens: 1234
tool_uses: 12
duration_ms: 45000</usage>"""

NOTIF_TAGS = """<usage><subagent_tokens>777</subagent_tokens>
<tool_uses>3</tool_uses><duration_ms>9000</duration_ms></usage>"""


class TestParseNotification:
    def test_colon_format(self):
        record = parse_notification(NOTIF_COLON)
        assert record == {"source": "notification", "total_tokens": 1234,
                          "tool_uses": 12, "duration_ms": 45000}

    def test_tag_format(self):
        record = parse_notification(NOTIF_TAGS)
        assert record["total_tokens"] == 777
        assert record["tool_uses"] == 3

    def test_no_usage_block_is_an_honest_miss(self):
        assert parse_notification("agent finished, no numbers") is None


class TestRecordUsage:
    def test_recording_and_dedup(self, project, tmp_path):
        root, lead = project
        notif = tmp_path / "notif.txt"
        notif.write_text(NOTIF_COLON, encoding="utf-8")
        first = engine.op_usage(root, "t", "a", "w1", notif)
        assert first == {"recorded": True, "total_tokens": 1234,
                         "tool_uses": 12, "duration_ms": 45000}
        # the same notification again: idempotent
        second = engine.op_usage(root, "t", "a", "w1", notif)
        assert second == {"recorded": False, "reason": "duplicate"}
        status = engine.op_status(root, "t")
        tokens = {t["id"]: t["tokens"] for t in status["tasks"]}
        assert tokens["a"] == 1234

    def test_notification_without_usage_rejected(self, project, tmp_path):
        root, _lead = project
        empty = tmp_path / "empty.txt"
        empty.write_text("nothing here", encoding="utf-8")
        with pytest.raises(UsageError, match="no <usage> block"):
            engine.op_usage(root, "t", "a", "w1", empty)


class TestCost:
    def test_full_split(self):
        cost, quality = compute_cost({"model": "glm-5.3", "total_tokens": None,
                                      "input_tokens": 500_000,
                                      "output_tokens": 500_000,
                                      "cache_read_tokens": 0})
        assert quality == "estimated"
        assert cost == pytest.approx((0.5 * 0.6 + 0.5 * 2.2), abs=1e-6)

    def test_total_only_splits_explicitly(self):
        cost, quality = compute_cost({"model": "glm-5.3", "total_tokens": 1_000_000})
        assert quality == "estimated"
        assert cost == pytest.approx(0.25 * 0.6 + 0.75 * 2.2, abs=1e-6)

    def test_unknown_model_is_unknown(self):
        assert compute_cost({"model": "mystery-9", "total_tokens": 5}) == \
            (None, "unknown")

    def test_missing_numbers_is_unknown(self):
        assert compute_cost({"model": "glm-5.3"}) == (None, "unknown")


class TestMetrics:
    def test_metrics_of_a_full_run(self, project):
        root, lead = project
        claim_a = engine.op_claim(root, "t", "a", "w1")
        claim_b = engine.op_claim(root, "t", "b", "w2")
        (root / "out" / "a.md").write_text("x", encoding="utf-8")
        (root / "out" / "b.md").write_text("y", encoding="utf-8")
        engine.op_submit(root, "t", "a", "w1", claim_a["execution_token"])
        engine.op_submit(root, "t", "b", "w2", claim_b["execution_token"])
        # task a fails once, then passes with the same still-valid token;
        # task b passes directly
        engine.op_verify(root, "t", "a", "fail", "fix it", lead)
        engine.op_submit(root, "t", "a", "w1", claim_a["execution_token"])
        engine.op_verify(root, "t", "a", "pass", "", lead)
        engine.op_verify(root, "t", "b", "pass", "", lead)
        engine.op_tokens(root, "t", "a", 1000, "w1", model="glm-5.3")
        engine.op_tokens(root, "t", "b", 500, "w2", model="glm-5.3")

        metrics = engine.op_metrics(root, "t")
        assert metrics["tasks_total"] == 2
        assert metrics["task_success_rate"] == 1.0
        assert metrics["verification_attempts"] == 3
        assert metrics["verification_pass_rate"] == round(2 / 3, 3)
        assert metrics["tokens_total"] == 1500
        assert metrics["cost_quality"] == "estimated"
        assert metrics["cost_total_usd"] > 0
        assert metrics["cost_per_successful_task"] == \
            pytest.approx(metrics["cost_total_usd"] / 2)
        assert metrics["sequential_execution_estimate_s"] is not None
        # hardening spec 6.1 counters
        assert metrics["execution_attempts"] == 2
        assert metrics["submissions"] == 2  # shared mode: rows still exist
        assert metrics["verification_first_pass_rate"] == 0.5
        assert metrics["required_criteria"] == 2
        assert metrics["speedup_quality"] in ("measured", "unknown")
