"""Immutable execution and integration consistency (hardening spec 2, 5)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import StateRejected
from agent_teams.storage import open_store


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def git_project(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agent-teams/\nignored.txt\n",
                                         encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@e.i")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base-0")
    (tmp_path / "second.txt").write_text("2\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base-1")
    return tmp_path


def task(tid, assignee="w1", **kw):
    base = {"id": tid, "title": tid, "assignee": assignee,
            "files_read": [], "files_write": [f"out/{tid}.md"],
            "acceptance": ["done"], "deps": []}
    base.update(kw)
    return base


def pass_ok(project, team, tid, lead, by="v", reason="test acceptance"):
    return engine.op_verify(project, team, tid, "pass", "", lead, by=by,
                            override_reason=reason)


def init_team(project, tasks, team="t"):
    spec_file = project / "spec.json"
    spec_file.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    created = engine.op_init(project, team, spec_file)
    git(project, "add", "-A")
    git(project, "commit", "-qm", "spec")
    return created


def claim_and_commit(project, team, tid, assignee="w1", text="work\n"):
    claim = engine.op_claim(project, team, tid, assignee)
    ws = Path(claim["workspace"])
    (ws / "out" / f"{tid}.md").write_text(text, encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", tid)
    return claim, ws


class TestCleanSubmit:
    def test_dirty_modified_rejected_with_file_list(self, git_project):
        created = init_team(git_project, [task("a")])
        claim, ws = claim_and_commit(git_project, "t", "a")
        (ws / "out" / "a.md").write_text("more\n", encoding="utf-8")
        with pytest.raises(StateRejected,
                           match="SUBMIT_REJECTED_DIRTY_WORKTREE") as exc:
            engine.op_submit(git_project, "t", "a", "w1",
                             claim["execution_token"])
        assert "out/a.md" in str(exc.value)

    def test_untracked_rejected(self, git_project):
        created = init_team(git_project, [task("a")])
        claim, ws = claim_and_commit(git_project, "t", "a")
        (ws / "stray.txt").write_text("x", encoding="utf-8")
        with pytest.raises(StateRejected, match="stray.txt"):
            engine.op_submit(git_project, "t", "a", "w1",
                             claim["execution_token"])

    def test_gitignored_files_do_not_block(self, git_project):
        created = init_team(git_project, [task("a")])
        claim, ws = claim_and_commit(git_project, "t", "a")
        (ws / "ignored.txt").write_text("x", encoding="utf-8")
        result = engine.op_submit(git_project, "t", "a", "w1",
                                  claim["execution_token"])
        assert result["status"] == "awaiting_verification"

    def test_non_ancestor_head_rejected(self, git_project):
        # base-0 is a sibling ancestor: HEAD on it lacks the run base
        first_base = git(git_project, "rev-list", "--max-parents=0",
                         "HEAD").strip().splitlines()[0]
        created = init_team(git_project, [task("a")])
        claim, ws = claim_and_commit(git_project, "t", "a")
        git(ws, "reset", "-q", "--hard", first_base)
        (ws / "out" / "a.md").write_text("other history\n", encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "off-base")
        with pytest.raises(StateRejected,
                           match="SUBMIT_REJECTED_DETACHED_HEAD"):
            engine.op_submit(git_project, "t", "a", "w1",
                             claim["execution_token"])


class TestImmutableSnapshot:
    def test_submission_pinned_and_recorded(self, git_project):
        created = init_team(git_project, [task("a")])
        claim, ws = claim_and_commit(git_project, "t", "a")
        result = engine.op_submit(git_project, "t", "a", "w1",
                                  claim["execution_token"])
        with open_store(git_project / ".agent-teams" / "t") as store:
            submission = store.get_submission(result["submission_id"])
        assert submission["commit_sha"] == result["commit_sha"]
        assert submission["verification_status"] == "pending"
        ref = (f"refs/agent-teams/t/a/{result['submission_id']}")
        pinned = git(git_project, "rev-parse", ref).strip()
        assert pinned == result["commit_sha"]

    def test_verification_uses_snapshot_not_live_tree(self, git_project):
        spec = {"tasks": [{
            "id": "a", "title": "a", "assignee": "w1",
            "files_write": ["out/a.md"],
            "acceptance": [
                {"id": "AC-1", "description": "file check",
                 "type": "file_check",
                 "verification": "test -f out/a.md"}],
            "deps": []}]}
        spec_file = git_project / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        created = engine.op_init(git_project, "t", spec_file)
        git(git_project, "add", "-A")
        git(git_project, "commit", "-qm", "spec")
        claim, ws = claim_and_commit(git_project, "t", "a")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        # the executor breaks the LIVE tree after submit: the snapshot
        # must still verify clean
        (ws / "out" / "a.md").unlink()
        outcome = pass_ok(git_project, "t", "a", created["lead_token"])
        assert outcome["status"] == "completed"

    def test_branch_moved_after_pass_not_integrated(self, git_project):
        created = init_team(git_project, [task("a")])
        lead = created["lead_token"]
        claim, ws = claim_and_commit(git_project, "t", "a")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        pass_ok(git_project, "t", "a", lead)
        # an unverified commit lands on the task branch after PASS
        (ws / "out" / "smuggled.md").write_text("not verified\n",
                                                encoding="utf-8")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "smuggle")
        result = engine.op_integrate(git_project, "t", lead)
        assert result["ok"] is True
        engine.op_apply(git_project, "t", lead)
        assert (git_project / "out" / "a.md").exists()
        assert not (git_project / "out" / "smuggled.md").exists()

    def test_missing_accepted_commit_rejected(self, git_project):
        created = init_team(git_project, [task("a")])
        lead = created["lead_token"]
        claim, ws = claim_and_commit(git_project, "t", "a")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        pass_ok(git_project, "t", "a", lead)
        with open_store(git_project / ".agent-teams" / "t") as store:
            with store.transaction():
                store.update_task("t", "a",
                                  accepted_commit_sha="0" * 40)
        with pytest.raises(StateRejected, match="missing from the repository"):
            engine.op_integrate(git_project, "t", lead)


class TestRunBase:
    def test_worktree_grows_from_run_base(self, git_project):
        created = init_team(git_project, [task("a")])
        run_base = created["run_base"]
        assert run_base
        # the project moves forward after init
        (git_project / "later.txt").write_text("x\n", encoding="utf-8")
        git(git_project, "add", "-A")
        git(git_project, "commit", "-qm", "later")
        claim, _ = claim_and_commit(git_project, "t", "a")
        with open_store(git_project / ".agent-teams" / "t") as store:
            task_row = store.get_task("t", "a")
        assert task_row["base_commit"] == run_base


class TestApplyGuards:
    def test_dirty_project_blocks_apply(self, git_project):
        created = init_team(git_project, [task("a")])
        lead = created["lead_token"]
        claim, _ = claim_and_commit(git_project, "t", "a")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        pass_ok(git_project, "t", "a", lead)
        engine.op_integrate(git_project, "t", lead)
        (git_project / "dirty.txt").write_text("x\n", encoding="utf-8")
        with pytest.raises(StateRejected,
                           match="APPLY_REJECTED_DIRTY_PROJECT"):
            engine.op_apply(git_project, "t", lead)
        assert engine.op_status(git_project, "t")["run"]["status"] \
            == "apply_pending"

    def test_moved_base_blocks_apply_then_reintegrate(self, git_project):
        created = init_team(git_project, [task("a")])
        lead = created["lead_token"]
        claim, _ = claim_and_commit(git_project, "t", "a")
        engine.op_submit(git_project, "t", "a", "w1", claim["execution_token"])
        pass_ok(git_project, "t", "a", lead)
        engine.op_integrate(git_project, "t", lead)
        (git_project / "moved.txt").write_text("x\n", encoding="utf-8")
        git(git_project, "add", "-A")
        git(git_project, "commit", "-qm", "moved")
        with pytest.raises(StateRejected, match="APPLY_REJECTED_BASE_MOVED"):
            engine.op_apply(git_project, "t", lead)
        # the honest path: re-integrate against the new base, then apply
        result = engine.op_integrate(git_project, "t", lead)
        assert result["ok"] is True
        engine.op_apply(git_project, "t", lead)
        assert (git_project / "out" / "a.md").exists()

    def test_topological_merge_order(self, git_project):
        created = init_team(git_project, [
            task("b", deps=["a"]), task("a")])
        lead = created["lead_token"]
        for tid in ("a", "b"):
            claim, _ = claim_and_commit(git_project, "t", tid)
            engine.op_submit(git_project, "t", tid, "w1",
                             claim["execution_token"])
            pass_ok(git_project, "t", tid, lead)
        result = engine.op_integrate(git_project, "t", lead)
        order = [entry["task"] for entry in result["merged"]]
        assert order.index("a") < order.index("b")
