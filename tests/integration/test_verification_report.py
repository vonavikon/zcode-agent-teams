"""Verification report enforcement (hardening spec 4)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import UsageError
from agent_teams.storage import open_store


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def project(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@e.i")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    spec = {"tasks": [{
        "id": "a", "title": "a", "assignee": "w1",
        "files_write": ["out/a.md"],
        "acceptance": [
            {"id": "AC-1", "description": "file present", "type": "file_check",
             "verification": "test -f out/a.md"},
            {"id": "AC-2", "description": "content sound", "type": "code_review"},
            {"id": "AC-3", "description": "taste", "type": "manual"},
        ], "deps": []}]}
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    created = engine.op_init(tmp_path, "t", spec_file)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "spec")
    claim = engine.op_claim(tmp_path, "t", "a", "w1")
    ws = Path(claim["workspace"])
    (ws / "out" / "a.md").write_text("work\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "work")
    submitted = engine.op_submit(tmp_path, "t", "a", "w1",
                                 claim["execution_token"])
    with open_store(tmp_path / ".agent-teams" / "t") as store:
        submission = store.get_submission(submitted["submission_id"])
    return {"root": tmp_path, "lead": created["lead_token"],
            "submission": submission}


def report_for(p, **overrides) -> dict:
    report = {
        "schema_version": "1.0",
        "team_id": "t",
        "task_id": "a",
        "submission_id": p["submission"]["id"],
        "commit_sha": p["submission"]["commit_sha"],
        "verifier": {"id": "v-1", "mode": "subagent"},
        "criteria": [
            {"criterion_id": "AC-1", "status": "pass",
             "evidence": [{"path": "out/a.md", "line_start": 1,
                           "claim": "file exists"}],
             "reason": "checked"},
            {"criterion_id": "AC-2", "status": "pass",
             "evidence": [{"path": "out/a.md", "line_start": 1,
                           "line_end": 1, "claim": "content sound"}],
             "reason": "reviewed"},
            {"criterion_id": "AC-3", "status": "pass",
             "reason": "taste is subjective, fine"},
        ],
        "verdict": "pass",
    }
    report.update(overrides)
    return report


def verify(p, report=None, **kw):
    return engine.op_verify(p["root"], "t", "a", "pass", "", p["lead"],
                            by="v-1", report=report, **kw)


class TestValidReport:
    def test_valid_report_passes(self, project):
        outcome = verify(project, report_for(project))
        assert outcome["status"] == "completed"
        with open_store(project["root"] / ".agent-teams" / "t") as store:
            submission = store.get_submission(project["submission"]["id"])
        assert submission["verification_status"] == "passed"
        assert submission["verifier"] == "v-1"
        assert submission["report"]["verdict"] == "pass"


class TestBlocking:
    def test_missing_report_blocks_pass(self, project):
        with pytest.raises(UsageError, match="VERIFICATION_REPORT_MISSING"):
            verify(project)

    def test_wrong_schema_version(self, project):
        with pytest.raises(UsageError, match="schema_version"):
            verify(project, report_for(project, schema_version="0.9"))

    def test_wrong_submission_id(self, project):
        report = report_for(project, submission_id="someone-elses")
        with pytest.raises(UsageError, match="submission_id"):
            verify(project, report=report)

    def test_wrong_sha(self, project):
        report = report_for(project, commit_sha="0" * 40)
        with pytest.raises(UsageError, match="commit_sha"):
            verify(project, report=report)

    def test_incomplete_coverage(self, project):
        report = report_for(project)
        report["criteria"] = report["criteria"][:2]
        with pytest.raises(UsageError, match="coverage"):
            verify(project, report=report)

    def test_unknown_criterion(self, project):
        report = report_for(project)
        report["criteria"].append({"criterion_id": "AC-99", "status": "pass"})
        with pytest.raises(UsageError, match="unknown criterion"):
            verify(project, report=report)

    def test_required_fail_blocks_pass_verdict(self, project):
        report = report_for(project)
        report["criteria"][1]["status"] = "fail"
        report["criteria"][1]["reason"] = "not sound"
        with pytest.raises(UsageError, match="failed required"):
            verify(project, report=report)

    def test_fail_without_reason_rejected(self, project):
        report = report_for(project)
        report["criteria"][2]["status"] = "fail"
        report["criteria"][2]["reason"] = ""
        report["verdict"] = "fail"
        with pytest.raises(UsageError, match="without a reason"):
            verify(project, report=report)

    def test_code_review_pass_needs_evidence(self, project):
        report = report_for(project)
        report["criteria"][1]["evidence"] = []
        with pytest.raises(UsageError, match="without evidence"):
            verify(project, report=report)

    def test_evidence_needs_line(self, project):
        report = report_for(project)
        report["criteria"][0]["evidence"] = [{"path": "out/a.md"}]
        with pytest.raises(UsageError, match="line_start"):
            verify(project, report=report)

    def test_manual_criterion_allows_reason_only(self, project):
        report = report_for(project)
        assert report["criteria"][2].get("evidence") is None
        outcome = verify(project, report=report)
        assert outcome["status"] == "completed"

    def test_wrong_team(self, project):
        with pytest.raises(UsageError, match="team_id"):
            verify(project, report_for(project, team_id="other"))


class TestOverride:
    def test_override_with_reason_audited(self, project):
        outcome = verify(project, override_reason="human checked it by hand")
        assert outcome["status"] == "completed"
        with open_store(project["root"] / ".agent-teams" / "t") as store:
            submission = store.get_submission(project["submission"]["id"])
        assert submission["verification_status"] == "overridden"
        events = (project["root"] / ".agent-teams" / "t" /
                  "events.jsonl").read_text(encoding="utf-8")
        assert "override" in events
        assert "human checked it by hand" in events

    def test_empty_override_reason_rejected(self, project):
        with pytest.raises(UsageError, match="VERIFICATION_REPORT_MISSING"):
            verify(project, override_reason="   ")

    def test_machine_gate_still_enforced_with_override(self, tmp_path):
        # an override cannot bypass a failing machine criterion
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
        (tmp_path / ".gitignore").write_text(".agent-teams/\n",
                                             encoding="utf-8")
        git(tmp_path, "init", "-q", "-b", "main")
        git(tmp_path, "config", "user.email", "t@e.i")
        git(tmp_path, "config", "user.name", "t")
        (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-qm", "base")
        spec = {"tasks": [{
            "id": "a", "title": "a", "assignee": "w1",
            "files_write": ["out/a.md"],
            "acceptance": [{"id": "AC-1", "description": "file",
                            "type": "file_check",
                            "verification": "test -f out/nope.md"}],
            "deps": []}]}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        created = engine.op_init(tmp_path, "t", spec_file)
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-qm", "spec")
        claim = engine.op_claim(tmp_path, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("x\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "w")
        engine.op_submit(tmp_path, "t", "a", "w1", claim["execution_token"])
        outcome = engine.op_verify(tmp_path, "t", "a", "pass", "",
                                   created["lead_token"],
                                   override_reason="trust me")
        assert outcome["status"] == "in_progress"  # forced FAIL
