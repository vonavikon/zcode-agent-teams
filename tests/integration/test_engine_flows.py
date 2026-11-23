"""End-to-end engine flows (spec section 11.4, scenarios A-F) and the
authority model, exercised through the operations layer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_teams import engine
from agent_teams.errors import (NotFoundError, PermissionDenied,
                                StateRejected, UsageError)
from agent_teams.storage import open_store


def spec(tasks):
    return {"tasks": tasks}


def task(tid, assignee, **kw):
    base = {"id": tid, "title": f"title {tid}", "assignee": assignee,
            "files_read": [], "files_write": [f"out/{tid}.md"],
            "acceptance": [f"{tid} done"], "deps": []}
    base.update(kw)
    return base


@pytest.fixture
def project(tmp_path):
    (tmp_path / "out").mkdir()
    return tmp_path


def init_team(project, tasks, **kw):
    spec_file = project / "tasks-spec.json"
    spec_file.write_text(json.dumps(spec(tasks)), encoding="utf-8")
    return engine.op_init(project, "t", spec_file, **kw)


def full_cycle(root, team, tid, assignee, verdicts, lead_token):
    """claim -> submit -> verify per verdict; returns last outcome."""
    claim = engine.op_claim(root, team, tid, assignee)
    outcome = None
    for verdict, feedback in verdicts:
        engine.op_submit(root, team, tid, assignee, claim["execution_token"])
        outcome = engine.op_verify(root, team, tid, verdict, feedback,
                                   lead_token)
    return claim, outcome


class TestScenarioA_ThreeParallelTasks:
    def test_all_pass(self, project):
        created = init_team(project, [task("a", "w1"), task("b", "w2"),
                                      task("c", "w3")])
        lead = created["lead_token"]
        for tid, assignee in (("a", "w1"), ("b", "w2"), ("c", "w3")):
            _, outcome = full_cycle(project, "t", tid, assignee,
                                    [("pass", "")], lead)
            assert outcome["status"] == "completed"
        status = engine.op_status(project, "t")
        assert all(t["status"] == "completed" for t in status["tasks"])
        # v0.2 mirrors were refreshed
        mirror = json.loads((project / ".agent-teams" / "t" / "tasks.json")
                            .read_text(encoding="utf-8"))
        assert [t["status"] for t in mirror["tasks"]] == ["completed"] * 3


class TestScenarioB_FailThenPass:
    def test_executor_gets_feedback_and_fixes(self, project):
        created = init_team(project, [task("api", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(project, "t", "api", "w1")
        engine.op_submit(project, "t", "api", "w1", claim["execution_token"])
        engine.op_verify(project, "t", "api", "fail", "expired token missed",
                         lead)
        messages = engine.op_read(project, "t", "w1")
        assert len(messages) == 1
        assert messages[0]["text"].startswith("VERIFY FAIL on task api (round 1)")
        # same attempt continues: the old execution token still works
        engine.op_submit(project, "t", "api", "w1", claim["execution_token"])
        outcome = engine.op_verify(project, "t", "api", "pass", "", lead)
        assert outcome == {"task": "api", "status": "completed",
                           "failed_rounds": 1}
        assert engine.op_read(project, "t", "w1") == []


class TestScenarioC_BlockReopenNewExecutor:
    def test_block_on_second_fail_then_release(self, project):
        created = init_team(project, [task("docs", "w1")])
        lead = created["lead_token"]
        _, outcome = full_cycle(project, "t", "docs", "w1",
                                [("fail", "f1"), ("fail", "f2")], lead)
        assert outcome["status"] == "blocked"  # the off-by-one is fixed
        assert outcome["failed_rounds"] == 2
        blocked_messages = engine.op_read(project, "t", "w1")
        texts = [m["text"] for m in blocked_messages]
        assert any(t.startswith("ROUND LIMIT on task docs") for t in texts)

        released = engine.op_release(project, "t", "docs", lead,
                                     reason="second chance")
        assert released["status"] == "pending"
        # a new executor claims and passes on a fresh verification cycle
        claim = engine.op_claim(project, "t", "docs", "w2")
        engine.op_submit(project, "t", "docs", "w2", claim["execution_token"])
        outcome = engine.op_verify(project, "t", "docs", "pass", "", lead)
        assert outcome == {"task": "docs", "status": "completed",
                           "failed_rounds": 0}

    def test_execution_retry_limit(self, project):
        created = init_team(project, [task("x", "w1")])
        lead = created["lead_token"]
        # Block and release twice: the default limit is 1 execution retry.
        full_cycle(project, "t", "x", "w1", [("fail", "1"), ("fail", "2")], lead)
        engine.op_release(project, "t", "x", lead)
        full_cycle(project, "t", "x", "w1", [("fail", "3"), ("fail", "4")], lead)
        with pytest.raises(StateRejected, match="execution retries"):
            engine.op_release(project, "t", "x", lead)


class TestScenarioD_AgentLoss:
    def test_recover_requeues_expired_lease(self, project):
        created = init_team(project, [task("a", "w1"), task("b", "w2")])
        lead = created["lead_token"]
        engine.op_claim(project, "t", "a", "w1")
        engine.op_claim(project, "t", "b", "w2")
        # expire a's lease directly in the database
        past = (datetime.now(timezone.utc) - timedelta(hours=2)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with open_store(project / ".agent-teams" / "t") as store:
            with store.transaction():
                store.update_task("t", "a", lease_expires_at=past)
        result = engine.op_recover(project, "t", lead)
        actions = {r["task"]: r["action"] for r in result["recovered"]}
        assert actions == {"a": "requeued"}
        status = {t["id"]: t["status"] for t in engine.op_status(project, "t")["tasks"]}
        assert status == {"a": "pending", "b": "in_progress"}

    def test_restart_limit_blocks(self, project):
        created = init_team(project, [task("a", "w1")])
        lead = created["lead_token"]
        engine.op_claim(project, "t", "a", "w1")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        team_dir = project / ".agent-teams" / "t"
        with open_store(team_dir) as store:
            with store.transaction():
                store.update_task("t", "a", restarts=2)  # default limit is 2
                store.update_task("t", "a", lease_expires_at=past)
        result = engine.op_recover(project, "t", lead)
        assert result["recovered"] == [{"task": "a", "action": "blocked"}]
        status = engine.op_status(project, "t")["tasks"][0]
        assert status["status"] == "blocked"


class TestScenarioE_WriteOverlap:
    def test_overlap_rejected_at_init(self, project):
        with pytest.raises(UsageError, match="overlapping paths"):
            init_team(project, [task("a", "w1", files_write=["src/x.py"]),
                                task("b", "w2", files_write=["src/x.py"])])

    def test_ordered_overlap_allowed_with_warning(self, project):
        created = init_team(project, [
            task("a", "w1", files_write=["src/x.py"]),
            task("b", "w2", files_write=["src/x.py"], deps=["a"])])
        assert any("overlap" in w for w in created["warnings"])


class TestScenarioF_Dependencies:
    def test_dep_must_be_completed_before_claim(self, project):
        created = init_team(project, [task("lib", "w1"),
                                      task("app", "w2", deps=["lib"])])
        lead = created["lead_token"]
        with pytest.raises(StateRejected, match="dependency lib"):
            engine.op_claim(project, "t", "app", "w2")
        full_cycle(project, "t", "lib", "w1", [("pass", "")], lead)
        engine.op_claim(project, "t", "app", "w2")  # now allowed


class TestAuthority:
    def test_verify_requires_lead_token(self, project):
        created = init_team(project, [task("a", "w1")])
        claim = engine.op_claim(project, "t", "a", "w1")
        engine.op_submit(project, "t", "a", "w1", claim["execution_token"])
        with pytest.raises(PermissionDenied, match="lead-token"):
            engine.op_verify(project, "t", "a", "pass", "", None)
        with pytest.raises(PermissionDenied, match="invalid lead token"):
            engine.op_verify(project, "t", "a", "pass", "", "forged")

    def test_executor_cannot_self_verify(self, project):
        created = init_team(project, [task("a", "w1")])
        claim = engine.op_claim(project, "t", "a", "w1")
        engine.op_submit(project, "t", "a", "w1", claim["execution_token"])
        # an executor knowing only its execution token still cannot verify
        with pytest.raises(PermissionDenied):
            engine.op_verify(project, "t", "a", "pass", "",
                             claim["execution_token"])

    def test_stale_execution_token_rejected(self, project):
        created = init_team(project, [task("a", "w1")])
        lead = created["lead_token"]
        _, outcome = full_cycle(project, "t", "a", "w1",
                                [("fail", "f1"), ("fail", "f2")], lead)
        assert outcome["status"] == "blocked"
        engine.op_release(project, "t", "a", lead)
        new_claim = engine.op_claim(project, "t", "a", "w2")
        with pytest.raises(PermissionDenied, match="stale"):
            engine.op_submit(project, "t", "a", "w2", "old-token-from-w1")
        engine.op_submit(project, "t", "a", "w2", new_claim["execution_token"])

    def test_wrong_owner_rejected(self, project):
        init_team(project, [task("a", "w1")])
        claim = engine.op_claim(project, "t", "a", "w1")
        with pytest.raises(PermissionDenied, match="owned by"):
            engine.op_submit(project, "t", "a", "w2", claim["execution_token"])

    def test_verifier_role_cannot_claim(self, project):
        spec_file = project / "spec.json"
        spec_file.write_text(json.dumps({"agents": [
            {"id": "w1", "role": "executor"}, {"id": "v", "role": "verifier"}],
            "tasks": [task("a", "w1")]}), encoding="utf-8")
        engine.op_init(project, "t", spec_file)
        with pytest.raises(StateRejected, match="only executors"):
            engine.op_claim(project, "t", "a", "v")


class TestGuards:
    def test_submit_requires_awaiting_flow(self, project):
        created = init_team(project, [task("a", "w1")])
        lead = created["lead_token"]
        # lead cannot verify a task that was not submitted
        claim = engine.op_claim(project, "t", "a", "w1")
        with pytest.raises(StateRejected, match="awaiting verification"):
            engine.op_verify(project, "t", "a", "pass", "", lead)

    def test_fail_requires_feedback(self, project):
        created = init_team(project, [task("a", "w1")])
        lead = created["lead_token"]
        claim = engine.op_claim(project, "t", "a", "w1")
        engine.op_submit(project, "t", "a", "w1", claim["execution_token"])
        with pytest.raises(UsageError, match="feedback"):
            engine.op_verify(project, "t", "a", "fail", "  ", lead)

    def test_unknown_team_and_task(self, project):
        with pytest.raises(NotFoundError):
            engine.op_status(project, "ghost")
        created = init_team(project, [task("a", "w1")])
        with pytest.raises(NotFoundError):
            engine.op_claim(project, "t", "nope", "w1")

    def test_heartbeat_extends_lease(self, project):
        created = init_team(project, [task("a", "w1")])
        claim = engine.op_claim(project, "t", "a", "w1")
        result = engine.op_heartbeat(project, "t", "w1", claim["execution_token"])
        assert result["task"] == "a"
        with pytest.raises(PermissionDenied):
            engine.op_heartbeat(project, "t", "w1", "wrong")

    def test_tokens_accumulate_and_mirror(self, project):
        created = init_team(project, [task("a", "w1")])
        engine.op_tokens(project, "t", "a", 100, "w1", model="test-model")
        result = engine.op_tokens(project, "t", "a", 50, "w1")
        assert result["tokens"] == 150
        mirror = json.loads((project / ".agent-teams" / "t" / "tasks.json")
                            .read_text(encoding="utf-8"))
        assert mirror["tasks"][0]["tokens"] == 150

    def test_cancel_pending(self, project):
        created = init_team(project, [task("a", "w1")])
        result = engine.op_cancel(project, "t", "a", created["lead_token"],
                                  reason="not needed")
        assert result["status"] == "cancelled"
