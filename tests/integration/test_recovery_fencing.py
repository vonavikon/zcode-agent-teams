"""Recovery and fencing (hardening spec 3)."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import PermissionDenied, StateRejected
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
    (tmp_path / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@e.i")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def init_team(project, config=None):
    tasks = [{"id": "a", "title": "a", "assignee": "w1",
              "files_write": ["out/a.md"], "acceptance": ["done"], "deps": []}]
    spec = {"config": config or {}, "tasks": tasks}
    spec_file = project / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    created = engine.op_init(project, "t", spec_file)
    git(project, "add", "-A")
    git(project, "commit", "-qm", "spec")
    return created


def ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def soon(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def set_task(project, tid, **fields):
    with open_store(project / ".agent-teams" / "t") as store:
        with store.transaction():
            store.update_task("t", tid, **fields)


class TestTimeouts:
    def test_heartbeat_timeout_fires_despite_valid_lease(self, git_project):
        created = init_team(git_project, {"execution_timeout_s": 3600,
                                          "heartbeat_timeout_s": 60})
        claim = engine.op_claim(git_project, "t", "a", "w1")
        # a fresh lease, but the last heartbeat is two hours old
        set_task(git_project, "a", heartbeat_at=ago(hours=2),
                 lease_expires_at=soon(minutes=30))
        result = engine.op_recover(git_project, "t", created["lead_token"])
        assert result["recovered"][0]["reason"] == "heartbeat timeout"

    def test_execution_timeout_fires_despite_fresh_heartbeats(self, git_project):
        # heartbeats cannot extend an attempt forever
        created = init_team(git_project, {"execution_timeout_s": 60,
                                          "heartbeat_timeout_s": 3600})
        engine.op_claim(git_project, "t", "a", "w1")
        set_task(git_project, "a", claimed_at=ago(hours=3),
                 heartbeat_at=ago(seconds=5),
                 lease_expires_at=soon(minutes=30))
        result = engine.op_recover(git_project, "t", created["lead_token"])
        assert result["recovered"][0]["reason"] == "execution timeout"

    def test_alive_executor_not_touched(self, git_project):
        created = init_team(git_project)
        engine.op_claim(git_project, "t", "a", "w1")
        result = engine.op_recover(git_project, "t", created["lead_token"])
        assert result["recovered"] == []


class TestFencing:
    def test_old_token_rejected_after_recovery(self, git_project):
        created = init_team(git_project)
        claim = engine.op_claim(git_project, "t", "a", "w1")
        set_task(git_project, "a", lease_expires_at=ago(minutes=5))
        engine.op_recover(git_project, "t", created["lead_token"])
        with pytest.raises(PermissionDenied):
            engine.op_submit(git_project, "t", "a", "w1",
                             claim["execution_token"])
        # the fenced executor no longer owns anything to beat for
        with pytest.raises((PermissionDenied, StateRejected)):
            engine.op_heartbeat(git_project, "t", "w1",
                                claim["execution_token"])

    def test_old_workspace_quarantined_not_reused(self, git_project):
        created = init_team(git_project)
        claim = engine.op_claim(git_project, "t", "a", "w1")
        old_ws = Path(claim["workspace"])
        (old_ws / "out" / "a.md").write_text("partial\n", encoding="utf-8")
        set_task(git_project, "a", lease_expires_at=ago(minutes=5))
        engine.op_recover(git_project, "t", created["lead_token"])

        # the old attempt dir moved to quarantine, content preserved
        task_root = git_project / ".agent-teams" / "t" / "tasks" / "a"
        assert not (task_root / "attempts" / "1").exists()
        quarantine = list((task_root / "quarantine").iterdir())
        assert len(quarantine) == 1
        assert (quarantine[0] / "workspace" / "out" / "a.md").read_text(
            encoding="utf-8") == "partial\n"

        # the new claim gets a different directory
        new_claim = engine.op_claim(git_project, "t", "a", "w2")
        assert new_claim["workspace"] != claim["workspace"]
        assert Path(new_claim["workspace"]).parts[-2] == "2"

    def test_two_executors_one_task(self, git_project):
        created = init_team(git_project)
        engine.op_claim(git_project, "t", "a", "w1")
        with pytest.raises(StateRejected,
                           match="not pending|is not allowed"):
            engine.op_claim(git_project, "t", "a", "w2")


class TestRecoveryReliability:
    def test_repeated_recover_is_a_noop(self, git_project):
        created = init_team(git_project)
        engine.op_claim(git_project, "t", "a", "w1")
        set_task(git_project, "a", lease_expires_at=ago(minutes=5))
        first = engine.op_recover(git_project, "t", created["lead_token"])
        assert first["recovered"][0]["action"] == "requeued"
        team_dir = git_project / ".agent-teams" / "t"
        events_before = (team_dir / "events.jsonl").read_text(
            encoding="utf-8").count("\n")
        second = engine.op_recover(git_project, "t", created["lead_token"])
        assert second["recovered"] == []
        events_after = (team_dir / "events.jsonl").read_text(
            encoding="utf-8").count("\n")
        assert events_after == events_before

    def test_crashed_process_recovered_end_to_end(self, git_project):
        created = init_team(git_project)
        engine.op_claim(git_project, "t", "a", "w1")
        # simulate the executor dying without any cleanup: nothing to kill
        # here because the executor is stateless between CLI calls; the
        # crash contract is "no further CLI calls ever happen", which is
        # exactly the stale-lease state
        set_task(git_project, "a", lease_expires_at=ago(seconds=1))
        result = engine.op_recover(git_project, "t", created["lead_token"])
        assert result["recovered"][0]["action"] == "requeued"
        status = engine.op_status(git_project, "t")["tasks"][0]
        assert status["status"] == "pending"
        # messages survive recovery
        engine.op_message(git_project, "t", "w1", "lead", "still here")
        got = engine.op_read(git_project, "t", "w1")
        assert got and got[0]["text"] == "still here"

    def test_restart_limit_blocks_exactly(self, git_project):
        created = init_team(git_project, {"max_agent_restarts": 1})
        lead = created["lead_token"]
        for _ in range(2):  # one recovery allowed, the second blocks
            engine.op_claim(git_project, "t", "a", "w1")
            set_task(git_project, "a", lease_expires_at=ago(seconds=1))
            result = engine.op_recover(git_project, "t", lead)
        assert result["recovered"][0]["action"] == "blocked"
        status = engine.op_status(git_project, "t")["tasks"][0]
        assert status["status"] == "blocked"
