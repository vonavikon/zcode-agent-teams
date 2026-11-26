"""Stage 5 extras: inspection commands and the HTML report."""
from __future__ import annotations

import json
from pathlib import Path

from agent_teams import engine


def init_two_tasks(tmp_path):
    (tmp_path / "out").mkdir()
    spec = {"tasks": [
        {"id": "lib", "title": "Library", "assignee": "w1",
         "files_write": ["out/lib.md"], "acceptance": ["done"], "deps": []},
        {"id": "app", "title": "Application", "assignee": "w2",
         "files_write": ["out/app.md"], "acceptance": ["done"],
         "deps": ["lib"]}]}
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return engine.op_init(tmp_path, "t", spec_file)


class TestPlan:
    def test_valid_spec(self, tmp_path):
        created = init_two_tasks(tmp_path)
        plan = engine.op_plan(tmp_path, tmp_path / "spec.json")
        assert plan["ok"] is True
        assert plan["topological_order"] == ["lib", "app"]

    def test_invalid_spec_lists_errors(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"tasks": [
            {"id": "a", "title": "x", "assignee": "w1", "acceptance": []}]}),
            encoding="utf-8")
        plan = engine.op_plan(tmp_path, bad)
        assert plan["ok"] is False
        assert any("E_ACCEPTANCE_EMPTY" in e for e in plan["errors"])


class TestInspection:
    def test_graph(self, tmp_path):
        init_two_tasks(tmp_path)
        graph = engine.op_graph(tmp_path, "t")
        assert graph["graph"] == ["lib -> app [pending]", "lib [pending]"]

    def test_agents(self, tmp_path):
        init_two_tasks(tmp_path)
        agents = engine.op_agents(tmp_path, "t")
        ids = {a["id"]: a["role"] for a in agents["agents"]}
        assert ids == {"w1": "executor", "w2": "executor"}

    def test_logs(self, tmp_path):
        created = init_two_tasks(tmp_path)
        claim = engine.op_claim(tmp_path, "t", "lib", "w1")
        logs = engine.op_logs(tmp_path, "t")
        types = [e["type"] for e in logs]
        assert "team_created" in types and "task_claimed" in types
        only_lib = engine.op_logs(tmp_path, "t", task_id="lib")
        assert all(e["task"] == "lib" for e in only_lib)

    def test_diff(self, tmp_path):
        created = init_two_tasks(tmp_path)
        claim = engine.op_claim(tmp_path, "t", "lib", "w1")
        (tmp_path / "out" / "lib.md").write_text("x", encoding="utf-8")
        engine.op_submit(tmp_path, "t", "lib", "w1", claim["execution_token"])
        diff = engine.op_diff(tmp_path, "t", "lib")
        # no git repository: shared mode records no file diff
        assert diff["changed_files"] == []
        assert diff["write_set_violations"] == []
        assert diff["base_commit"] is None

    def test_cost(self, tmp_path):
        created = init_two_tasks(tmp_path)
        engine.op_tokens(tmp_path, "t", "lib", 1000, "w1", model="glm-5.3")
        cost = engine.op_cost(tmp_path, "t")
        assert cost["per_task"]["lib"]["tokens"] == 1000
        assert cost["total_usd"] > 0
        assert cost["quality"] == "estimated"

    def test_workspace_listing(self, tmp_path):
        created = init_two_tasks(tmp_path)
        result = engine.op_workspace(tmp_path, "t")
        assert result == {"workspaces": []}


class TestReport:
    """The report of the team's work (v2.1 spec, section 18), through the
    command the lead runs. The team is in shared mode: with no git project
    the files a stage declared are its result. The whole contract of the
    page is pinned in tests/unit/test_report_v21.py."""

    SECTIONS = ["sec-goal", "sec-result", "sec-done", "sec-not-done", "sec-problems",
                "sec-how-solved", "sec-quality", "sec-cost", "sec-time", "sec-team",
                "sec-history", "sec-tech"]

    def test_html_report_self_contained(self, tmp_path):
        created = init_two_tasks(tmp_path)
        lead = created["lead_token"]
        claim = engine.op_claim(tmp_path, "t", "lib", "w1")
        (tmp_path / "out" / "lib.md").write_text("x", encoding="utf-8")
        engine.op_submit(tmp_path, "t", "lib", "w1", claim["execution_token"])
        engine.op_verify(tmp_path, "t", "lib", "pass", "", lead)
        out = tmp_path / "report.html"
        result = engine.op_report(tmp_path, "t", out)
        html_text = Path(result["report"]).read_text(encoding="utf-8")
        assert out.exists()
        assert html_text.startswith("<!DOCTYPE html>")
        assert "http" not in html_text  # self-contained, no external links
        assert "<script" not in html_text and "<link" not in html_text
        assert "\u2014" not in html_text and "\u2013" not in html_text
        # twelve sections with stable ids, in the order of the spec
        assert html_text.count('id="sec-') == 12
        places = [html_text.index(f'id="{section_id}"') for section_id in self.SECTIONS]
        assert places == sorted(places)
        # said in Russian, by the titles of the stages, in the words of the board
        assert "Что выполнено" in html_text and "Что не удалось выполнить" in html_text
        assert "Library" in html_text and "Application" in html_text
        assert "Принятая версия результата." in html_text
        assert "Проверка пройдена" in html_text      # a real check, in shared mode
        assert "Принято вручную" not in html_text    # nobody decided over a check
        # the identifiers of the stages stay inside the one closed details element
        before, inside = html_text.split("<details")
        assert "<code>lib</code>" in inside and "<code>app</code>" in inside
        assert "<code>lib</code>" not in before and "<code>app</code>" not in before
