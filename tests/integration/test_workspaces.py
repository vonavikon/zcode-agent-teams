"""Workspace isolation (spec section 5) on real git repositories."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_teams import engine, workspaces
from agent_teams.errors import ExternalError


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


def pass_ok(project, team, tid, lead, by="v", reason="test acceptance"):
    return engine.op_verify(project, team, tid, "pass", "", lead, by=by,
                            override_reason=reason)


def init_team(project, tasks, **config):
    spec = {"config": config, "tasks": tasks}
    spec_file = project / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return engine.op_init(project, "t", spec_file)


def task(tid, assignee, **kw):
    base = {"id": tid, "title": f"title {tid}", "assignee": assignee,
            "files_read": [], "files_write": [f"out/{tid}.md"],
            "acceptance": ["done"], "deps": []}
    base.update(kw)
    return base


class TestWorktreeCreation:
    def test_claim_creates_worktree_and_branch(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        assert ws.exists() and (ws / ".git").exists()
        # .agent-teams/t/tasks/a/attempts/1/workspace (per-attempt dirs)
        assert "attempts" in ws.parts and ws.parts[-2] == "1"
        row = engine.op_status(git_project, "t")["tasks"][0]
        assert claim["execution_token"]
        # the branch exists and records the base commit
        base = git(git_project, "rev-parse", "HEAD")
        branch = git(git_project, "rev-parse",
                     workspaces.branch_name("t", "a"))
        assert base.strip() == branch.strip()

    def test_second_claim_reuses_worktree(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        lead = created["lead_token"]
        first = engine.op_claim(git_project, "t", "a", "w1")
        (Path(first["workspace"]) / "out" / "a.md").write_text("draft",
                                                               encoding="utf-8")
        git(first["workspace"], "add", "-A")
        git(first["workspace"], "commit", "-qm", "draft")
        # two failed rounds block the task; release and reclaim: the
        # committed work must survive
        engine.op_submit(git_project, "t", "a", "w1", first["execution_token"])
        engine.op_verify(git_project, "t", "a", "fail", "f", lead)
        engine.op_submit(git_project, "t", "a", "w1", first["execution_token"])
        engine.op_verify(git_project, "t", "a", "fail", "f2", lead)
        engine.op_release(git_project, "t", "a", lead)
        second = engine.op_claim(git_project, "t", "a", "w2")
        # a new attempt gets a fresh directory (fencing), grown from the
        # last submitted checkpoint: the committed draft carries over
        assert second["workspace"] != first["workspace"]
        assert Path(second["workspace"]).parts[-2] == "2"
        assert (Path(second["workspace"]) / "out" / "a.md").read_text(
            encoding="utf-8") == "draft"


class TestDiffCapture:
    def test_allowed_writes_clean(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("result\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "work")
        result = engine.op_submit(git_project, "t", "a", "w1",
                                  claim["execution_token"])
        assert result["changed_files"] == 1
        assert result["write_set_violations"] == []
        assert result["submission_id"]
        assert result["commit_sha"].startswith(git(ws, "rev-parse", "HEAD").strip()[:8])

    def test_writes_outside_files_write_recorded(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("ok\n", encoding="utf-8")
        (ws / "src" / "engine.py").parent.mkdir(exist_ok=True)
        (ws / "src" / "engine.py").write_text("hacked\n", encoding="utf-8")
        (ws / "README.md").write_text("tampered\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "work with violations")
        result = engine.op_submit(git_project, "t", "a", "w1",
                                  claim["execution_token"])
        assert sorted(result["write_set_violations"]) == [
            "README.md", "src/engine.py"]
        # the lead sees the violation in inspect
        report = engine.op_inspect(git_project, "t", "a")
        assert report["task"]["metadata"]["workspace_violations"] == [
            "README.md", "src/engine.py"]

    def test_committed_changes_captured(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("committed\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "work")
        result = engine.op_submit(git_project, "t", "a", "w1",
                                  claim["execution_token"])
        assert result["changed_files"] == 1
        assert result["write_set_violations"] == []

    def test_directory_prefix_counts_as_declared(self, git_project):
        created = init_team(git_project, [task("a", "w1",
                                               files_write=["out"])])
        claim = engine.op_claim(git_project, "t", "a", "w1")
        (Path(claim["workspace"]) / "out" / "deep" / "file.md").parent.mkdir(
            parents=True, exist_ok=True)
        (Path(claim["workspace"]) / "out" / "deep" / "file.md").write_text(
            "x", encoding="utf-8")
        git(claim["workspace"], "add", "-A")
        git(claim["workspace"], "commit", "-qm", "work")
        result = engine.op_submit(git_project, "t", "a", "w1",
                                  claim["execution_token"])
        assert result["write_set_violations"] == []


class TestFallbacks:
    def test_non_git_project_shared_fallback_with_warning(self, tmp_path):
        (tmp_path / "out").mkdir()
        created = init_team(tmp_path, [task("a", "w1")])  # auto, no git
        assert any("NO isolation" in w for w in created["warnings"])
        claim = engine.op_claim(tmp_path, "t", "a", "w1")
        assert "workspace" not in claim
        result = engine.op_submit(tmp_path, "t", "a", "w1",
                                  claim["execution_token"])
        assert "changed_files" not in result

    def test_explicit_worktree_without_git_rejected(self, tmp_path):
        from agent_teams.errors import UsageError

        with pytest.raises(UsageError, match="not a git repository"):
            init_team(tmp_path, [task("a", "w1")], workspace="worktree")

    def test_shared_mode_no_isolation(self, tmp_path):
        (tmp_path / "out").mkdir()
        created = init_team(tmp_path, [task("a", "w1")], workspace="shared")
        assert any("NO isolation" in w for w in created["warnings"])


class TestLifecycle:
    def test_workspace_removed_after_integration(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("final\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "final")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        pass_ok(git_project, "t", "a", lead)

        from agent_teams.storage import open_store

        team_root = git_project / ".agent-teams" / "t"
        with open_store(team_root) as store:
            row = store.get_task("t", "a")
            # keep the branch: the worktree is gone, the work is recoverable
            workspaces.remove_workspace(git_project, team_root, "t", "a",
                                        keep_branch=True)
            assert not ws.exists()
            info = workspaces.create_workspace(git_project, team_root, "t", "a",
                                               base_commit=row["base_commit"])
            assert (Path(info.path) / "out" / "a.md").read_text(
                encoding="utf-8") == "final\n"
            # full cleanup removes both the worktree and the branch
            workspaces.remove_workspace(git_project, team_root, "t", "a")
            assert not Path(info.path).exists()
            proc = subprocess.run(
                ["git", "rev-parse", "--verify",
                 f"refs/heads/{workspaces.branch_name('t', 'a')}"],
                cwd=str(git_project), capture_output=True, text=True)
            assert proc.returncode != 0

    def test_blocked_task_keeps_workspace(self, git_project):
        created = init_team(git_project, [task("a", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(git_project, "t", "a", "w1")
        ws = Path(claim["workspace"])
        (ws / "out" / "a.md").write_text("partial\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "partial")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        engine.op_verify(git_project, "t", "a", "fail", "1", lead)
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        engine.op_verify(git_project, "t", "a", "fail", "2", lead)
        assert ws.exists()  # intermediate work is not lost
        assert (ws / "out" / "a.md").read_text(encoding="utf-8") == "partial\n"
