"""Unit tests for the state machine: allowed and forbidden edges."""
from __future__ import annotations

import pytest

from agent_teams import statemachine as sm
from agent_teams.errors import StateRejected
from agent_teams.storage import open_store


@pytest.fixture
def conn(tmp_path):
    with open_store(tmp_path) as store:
        with store.transaction():
            store.create_team({"id": "t1", "project_root": "."})
            store.upsert_task({"team_id": "t1", "id": "a", "title": "d",
                               "assignee": "w1"})
        yield store.conn


def task_row(conn, tid="a"):
    return conn.execute("SELECT * FROM tasks WHERE team_id='t1' AND id=?",
                        (tid,)).fetchone()


def status_of(conn, tid="a"):
    return task_row(conn, tid)["status"]


class TestAllowedEdges:
    def test_claim(self, conn):
        sm.apply_transition(conn, task_row(conn), sm.TaskStatus.IN_PROGRESS,
                            event_type="task_claimed", run_id="r", actor_id="w1",
                            initiator=sm.EXECUTOR, timestamp="2026-01-01T00:00:00.000Z")
        assert status_of(conn) == "in_progress"
        assert task_row(conn)["claimed_at"] == "2026-01-01T00:00:00.000Z"

    def test_submit(self, conn):
        sm.apply_transition(conn, task_row(conn), sm.IN_PROGRESS,
                            event_type="task_claimed", run_id="r", actor_id="w1",
                            initiator=sm.EXECUTOR, timestamp="2026-01-01T00:00:00.000Z")
        sm.apply_transition(conn, task_row(conn), sm.AWAITING_VERIFICATION,
                            event_type="task_submitted", run_id="r", actor_id="w1",
                            initiator=sm.EXECUTOR, timestamp="2026-01-01T00:00:01.000Z")
        assert status_of(conn) == "awaiting_verification"

    def test_pass(self, conn):
        for target, etype, init in (
                (sm.IN_PROGRESS, "task_claimed", sm.EXECUTOR),
                (sm.AWAITING_VERIFICATION, "task_submitted", sm.EXECUTOR),
                (sm.COMPLETED, "verification_passed", sm.GATE)):
            sm.apply_transition(conn, task_row(conn), target, event_type=etype,
                                run_id="r", actor_id="x", initiator=init,
                                timestamp="2026-01-01T00:00:00.000Z")
        assert status_of(conn) == "completed"

    def test_fail_returns_to_in_progress(self, conn):
        for target, etype, init in (
                (sm.IN_PROGRESS, "task_claimed", sm.EXECUTOR),
                (sm.AWAITING_VERIFICATION, "task_submitted", sm.EXECUTOR),
                (sm.IN_PROGRESS, "verification_failed", sm.GATE)):
            sm.apply_transition(conn, task_row(conn), target, event_type=etype,
                                run_id="r", actor_id="x", initiator=init,
                                timestamp="2026-01-01T00:00:00.000Z")
        # the FAIL path keeps the claim fields
        row = task_row(conn)
        assert row["claimed_at"] and row["status"] == "in_progress"

    def test_gate_blocks(self, conn):
        for target, etype, init in (
                (sm.IN_PROGRESS, "task_claimed", sm.EXECUTOR),
                (sm.AWAITING_VERIFICATION, "task_submitted", sm.EXECUTOR),
                (sm.BLOCKED, "task_blocked", sm.GATE)):
            sm.apply_transition(conn, task_row(conn), target, event_type=etype,
                                run_id="r", actor_id="x", initiator=init,
                                timestamp="2026-01-01T00:00:00.000Z",
                                reason="two fails")
        assert status_of(conn) == "blocked"

    def test_lead_releases_blocked(self, conn):
        for target, etype, init, kw in (
                (sm.IN_PROGRESS, "task_claimed", sm.EXECUTOR, {}),
                (sm.AWAITING_VERIFICATION, "task_submitted", sm.EXECUTOR, {}),
                (sm.BLOCKED, "task_blocked", sm.GATE, {"reason": "x"}),
                (sm.PENDING, "task_released", sm.LEAD,
                 {"reason": "reopen"})):
            sm.apply_transition(conn, task_row(conn), target, event_type=etype,
                                run_id="r", actor_id="x", initiator=init,
                                timestamp="2026-01-01T00:00:00.000Z", **kw)
        row = task_row(conn)
        assert row["status"] == "pending" and row["owner"] is None

    def test_lead_cancels_pending(self, conn):
        sm.apply_transition(conn, task_row(conn), sm.CANCELLED,
                            event_type="task_cancelled", run_id="r", actor_id="lead",
                            initiator=sm.LEAD, timestamp="2026-01-01T00:00:00.000Z",
                            reason="not needed")
        assert status_of(conn) == "cancelled"


class TestTheLeadCancelsAStageInWork:
    """v2.1 spec 17: the owner asks to drop a stage that already runs."""

    @pytest.mark.parametrize("source", ["in_progress", "awaiting_verification"])
    def test_allowed_for_the_lead_only(self, conn, source):
        conn.execute("UPDATE tasks SET status = ? WHERE team_id='t1' AND id='a'", (source,))
        sm.apply_transition(conn, task_row(conn), sm.CANCELLED,
                            event_type="task_cancelled", run_id="r", actor_id="lead",
                            initiator=sm.LEAD, timestamp="2026-01-01T00:00:00.000Z",
                            reason="not needed")
        assert status_of(conn) == "cancelled"


class TestForbiddenEdges:
    @pytest.mark.parametrize("source,target,initiator", [
        ("completed", "pending", "lead"),
        ("completed", "in_progress", "executor"),
        ("cancelled", "pending", "lead"),
        ("pending", "completed", "gate"),
        ("pending", "awaiting_verification", "executor"),
        ("pending", "blocked", "gate"),
        ("in_progress", "pending", "executor"),
        ("in_progress", "completed", "executor"),
        ("in_progress", "cancelled", "executor"),      # only the lead cancels
        ("awaiting_verification", "cancelled", "executor"),
        ("blocked", "cancelled", "lead"),              # a blocked stage goes back first
        ("blocked", "in_progress", "executor"),
        ("blocked", "completed", "gate"),
    ])
    def test_rejected(self, conn, source, target, initiator):
        conn.execute("UPDATE tasks SET status = ? WHERE team_id='t1' AND id='a'",
                     (source,))
        with pytest.raises(StateRejected):
            sm.apply_transition(conn, task_row(conn), sm.TaskStatus(target),
                                event_type="task_claimed", run_id="r",
                                actor_id="x", initiator=initiator,
                                timestamp="2026-01-01T00:00:00.000Z",
                                reason="r")

    def test_wrong_initiator_rejected(self, conn):
        with pytest.raises(StateRejected, match="cannot be initiated"):
            sm.apply_transition(conn, task_row(conn), sm.IN_PROGRESS,
                                event_type="task_claimed", run_id="r",
                                actor_id="x", initiator=sm.GATE,
                                timestamp="2026-01-01T00:00:00.000Z")

    def test_block_requires_reason(self, conn):
        conn.execute("UPDATE tasks SET status='in_progress' "
                     "WHERE team_id='t1' AND id='a'")
        with pytest.raises(StateRejected, match="reason"):
            sm.apply_transition(conn, task_row(conn), sm.BLOCKED,
                                event_type="task_blocked", run_id="r",
                                actor_id="lead", initiator=sm.LEAD,
                                timestamp="2026-01-01T00:00:00.000Z")

    def test_concurrent_writer_detected(self, conn):
        # The guard on the previous status catches a row changed between the
        # read and the write.
        row = task_row(conn)
        conn.execute("UPDATE tasks SET status='blocked' "
                     "WHERE team_id='t1' AND id='a'")
        with pytest.raises(StateRejected, match="another writer"):
            sm.apply_transition(conn, row, sm.IN_PROGRESS,
                                event_type="task_claimed", run_id="r",
                                actor_id="x", initiator=sm.EXECUTOR,
                                timestamp="2026-01-01T00:00:00.000Z")


class TestEventPerTransition:
    def test_event_row_written(self, conn):
        sm.apply_transition(conn, task_row(conn), sm.IN_PROGRESS,
                            event_type="task_claimed", run_id="r",
                            actor_id="w1", initiator=sm.EXECUTOR,
                            timestamp="2026-01-01T00:00:00.000Z",
                            metadata={"assignee": "w1"})
        row = conn.execute(
            "SELECT * FROM events WHERE team_id='t1' AND task_id='a'").fetchone()
        assert row["event_type"] == "task_claimed"
        assert row["actor_id"] == "w1"
        assert '"from": "pending"' in row["metadata_json"]
        assert '"to": "in_progress"' in row["metadata_json"]
