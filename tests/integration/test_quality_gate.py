"""Quality gate (spec section 7): machine checks and the integration gate."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import ExternalError, StateRejected


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def git_project(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "test")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def init_team(project, tasks):
    spec_file = project / "spec.json"
    spec_file.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    created = engine.op_init(project, "t", spec_file)
    git(project, "add", "-A")
    git(project, "commit", "-qm", "team spec")
    return created


def checked_task(tid, command, **kw):
    base = {"id": tid, "title": f"title {tid}", "assignee": "w1",
            "files_read": [], "files_write": [f"out/{tid}.md"],
            "acceptance": [
                {"id": "AC-1", "description": "machine check",
                 "type": "test", "verification": command},
                {"id": "AC-2", "description": "human judgement",
                 "type": "code_review"},
            ],
            "deps": []}
    base.update(kw)
    return base


def complete(project, tid, assignee="w1"):
    claim = engine.op_claim(project, "t", tid, assignee)
    ws = Path(claim.get("workspace", project))
    target = ws / "out" / f"{tid}.md"
    if target.parent != ws:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"work {tid}\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", f"work {tid}")
    engine.op_submit(project, "t", tid, assignee, claim["execution_token"])
    return claim


class TestMachineGate:
    def test_pass_blocked_by_failing_required_criterion(self, git_project):
        created = init_team(git_project, [checked_task("a", "exit 3")])
        lead = created["lead_token"]
        complete(git_project, "a")
        outcome = engine.op_verify(git_project, "t", "a", "pass", "", lead)
        assert outcome["status"] == "in_progress"  # forced FAIL
        messages = engine.op_read(git_project, "t", "w1")
        assert "Quality Gate override" in messages[0]["text"]
        assert "AC-1" in messages[0]["text"]
        report = engine.op_inspect(git_project, "t", "a")
        attempt = report["attempts"][0]
        assert attempt["verdict"] == "fail"
        assert "Quality Gate override" in attempt["feedback"]

    def test_pass_allowed_when_machine_green(self, git_project):
        (git_project / "check.py").write_text(
            "import sys\nsys.exit(0)\n", encoding="utf-8")
        git(git_project, "add", "-A")
        git(git_project, "commit", "-qm", "check")
        created = init_team(git_project,
                            [checked_task("a", "python3 check.py")])
        lead = created["lead_token"]
        complete(git_project, "a")
        outcome = engine.op_verify(git_project, "t", "a", "pass", "", lead)
        assert outcome["status"] == "completed"

    def test_optional_criterion_does_not_block(self, git_project):
        task_spec = checked_task("a", "exit 3")
        task_spec["acceptance"][0]["required"] = False
        created = init_team(git_project, [task_spec])
        lead = created["lead_token"]
        complete(git_project, "a")
        outcome = engine.op_verify(git_project, "t", "a", "pass", "", lead)
        assert outcome["status"] == "completed"

    def test_violation_blocks_pass(self, git_project):
        created = init_team(git_project, [checked_task("a", "exit 0")])
        lead = created["lead_token"]
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("ok\n", encoding="utf-8")
        (ws / "evil.txt").write_text("outside\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "work")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        outcome = engine.op_verify(git_project, "t", "a", "pass", "", lead)
        assert outcome["status"] == "in_progress"
        messages = engine.op_read(git_project, "t", "w1")
        assert "write outside files_write: evil.txt" in messages[0]["text"]

    def test_command_runs_in_task_workspace(self, git_project):
        (git_project / "check.py").write_text(
            "import sys, pathlib\n"
            "ok = pathlib.Path('out/a.md').exists()\n"
            "sys.exit(0 if ok else 5)\n", encoding="utf-8")
        git(git_project, "add", "-A")
        git(git_project, "commit", "-qm", "check")
        created = init_team(git_project,
                            [checked_task("a", "python3 check.py")])
        lead = created["lead_token"]
        complete(git_project, "a")  # writes out/a.md in the workspace
        outcome = engine.op_verify(git_project, "t", "a", "pass", "", lead)
        assert outcome["status"] == "completed"


class TestIntegrationGate:
    def test_successful_integration(self, git_project):
        created = init_team(git_project, [
            checked_task("a", "exit 0"), checked_task("b", "exit 0")])
        lead = created["lead_token"]
        complete(git_project, "a", "w1")
        engine.op_verify(git_project, "t", "a", "pass", "", lead)
        complete(git_project, "b", "w1")
        engine.op_verify(git_project, "t", "b", "pass", "", lead)

        (git_project / "itest.py").write_text(
            "import sys, pathlib\n"
            "sys.exit(0 if pathlib.Path('out/b.md').exists() else 9)\n",
            encoding="utf-8")
        git(git_project, "add", "-A")
        git(git_project, "commit", "-qm", "itest")

        result = engine.op_integrate(git_project, "t", lead,
                                     check_command="python3 itest.py")
        assert result["ok"] is True
        merged_shas = {entry["sha"] for entry in result["merged"]}
        assert len(merged_shas) == 2
        # the merged tree has both tasks' files
        ws = Path(result["workspace"])
        assert (ws / "out" / "a.md").exists()
        assert (ws / "out" / "b.md").exists()
        # nothing applied to the project yet: the run waits for apply
        status = engine.op_status(git_project, "t")
        assert status["run"]["status"] == "apply_pending"
        assert not (git_project / "out" / "a.md").exists()
        applied = engine.op_apply(git_project, "t", lead)
        assert applied == {"applied": True, "run": "finished"}
        assert (git_project / "out" / "a.md").exists()
        assert engine.op_status(git_project, "t")["run"]["status"] == "finished"

    def test_apply_merges_into_project(self, git_project):
        created = init_team(git_project, [checked_task("a", "exit 0")])
        lead = created["lead_token"]
        complete(git_project, "a", "w1")
        engine.op_verify(git_project, "t", "a", "pass", "", lead)
        result = engine.op_integrate(git_project, "t", lead, apply=True)
        assert result["applied"] is True
        assert (git_project / "out" / "a.md").exists()

    def test_conflict_stops_integration(self, git_project):
        # two tasks on the same file: allowed explicitly, run in parallel,
        # guaranteed merge conflict
        spec = {"config": {"allow_write_overlaps": True,
                           "allow_write_overlaps_reason": "test conflict"},
                "tasks": [checked_task("a", "exit 0",
                                       files_write=["out/shared.md"]),
                          checked_task("b", "exit 0",
                                       files_write=["out/shared.md"])]}
        spec_file = git_project / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        created = engine.op_init(git_project, "t", spec_file)
        lead = created["lead_token"]
        for tid, text in (("a", "version A\n"), ("b", "version B\n")):
            claim = engine.op_claim(git_project, "t", tid, "w1")
            ws = Path(claim["workspace"])
            (ws / "out" / "shared.md").write_text(text, encoding="utf-8")
            git(ws, "add", "-A")
            git(ws, "commit", "-qm", tid)
            engine.op_submit(git_project, "t", tid, "w1",
                             claim["execution_token"])
            engine.op_verify(git_project, "t", tid, "pass", "", lead)
        result = engine.op_integrate(git_project, "t", lead)
        assert result["ok"] is False
        assert result["conflicts"]["files"] == ["out/shared.md"]
        assert result["conflicts"]["sha"]
        # the run stops at integration_failed: the team is not completed
        status = engine.op_status(git_project, "t")
        assert status["run"]["status"] == "integration_failed"
        # and the project branch is untouched
        assert not (git_project / "out" / "shared.md").exists()

    def test_failed_check_stops_integration(self, git_project):
        created = init_team(git_project, [checked_task("a", "exit 0")])
        lead = created["lead_token"]
        complete(git_project, "a", "w1")
        engine.op_verify(git_project, "t", "a", "pass", "", lead)
        result = engine.op_integrate(git_project, "t", lead,
                                     check_command="exit 7")
        assert result["ok"] is False
        assert result["check"]["exit_code"] == 7
        status = engine.op_status(git_project, "t")
        assert status["run"]["status"] == "integration_failed"

    def test_incomplete_team_rejected(self, git_project):
        created = init_team(git_project, [checked_task("a", "exit 0")])
        lead = created["lead_token"]
        with pytest.raises(StateRejected, match="not completed"):
            engine.op_integrate(git_project, "t", lead)

    def test_lead_token_required(self, git_project):
        created = init_team(git_project, [checked_task("a", "exit 0")])
        with pytest.raises(Exception):
            engine.op_integrate(git_project, "t", "forged")

    def test_shared_mode_finishes_without_merge(self, tmp_path):
        (tmp_path / "out").mkdir()
        spec = {"tasks": [{
            "id": "a", "title": "t", "assignee": "w1", "files_write": [],
            "acceptance": ["done"], "deps": []}]}
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        created = engine.op_init(tmp_path, "t", spec_file)
        lead = created["lead_token"]
        claim = engine.op_claim(tmp_path, "t", "a", "w1")
        engine.op_submit(tmp_path, "t", "a", "w1", claim["execution_token"])
        engine.op_verify(tmp_path, "t", "a", "pass", "", lead)
        result = engine.op_integrate(tmp_path, "t", lead)
        assert result == {"ok": True, "mode": "shared", "applied": False}
        assert engine.op_status(tmp_path, "t")["run"]["status"] == "finished"
