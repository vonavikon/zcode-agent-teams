"""Unit tests for the event log and its hash chain."""
from __future__ import annotations

import json
import uuid

import pytest

from agent_teams import events
from agent_teams.errors import UsageError
from agent_teams.storage import open_store


@pytest.fixture
def store(tmp_path):
    with open_store(tmp_path) as s:
        with s.transaction():
            s.create_team({"id": "t1", "project_root": "."})
        yield s


def append(store, **kwargs):
    defaults = dict(event_type="task_created", team_id="t1", run_id="r1",
                    actor_id="lead")
    defaults.update(kwargs)
    with store.transaction():
        return events.append_event(store.conn, **defaults)


class TestAppend:
    def test_inside_transaction(self, store):
        row = append(store, task_id="a")
        assert row["seq"] == 1
        assert row["prev_hash"] == events.GENESIS
        assert row["hash"]
        assert row["event_id"]

    def test_required_fields(self, store):
        # The database enforces NOT NULL for run_id/team_id.
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            append(store, team_id=None, run_id=None)

    def test_unknown_type_rejected(self, store):
        with pytest.raises(UsageError, match="unknown event type"):
            append(store, event_type="party_started")

    @pytest.mark.parametrize("etype", sorted(events.EVENT_TYPES))
    def test_every_known_type_accepted(self, store, etype):
        append(store, event_type=etype)

    def test_chain_links(self, store):
        first = append(store)
        second = append(store)
        assert second["prev_hash"] == first["hash"]

    def test_reappend_same_logic_new_ids(self, store):
        first = append(store, task_id="a")
        again = append(store, task_id="a")
        assert again["event_id"] != first["event_id"]
        assert again["hash"] != first["hash"]


class TestVerifyChain:
    def test_ok_after_many(self, store):
        for i in range(10):
            append(store, task_id=f"t{i}")
        check = events.verify_chain(store.conn, "t1")
        assert check.ok and check.events_checked == 10

    def test_edit_detected_at_exact_seq(self, store):
        for i in range(5):
            append(store, task_id=f"t{i}")
        with store.transaction():
            store.run("UPDATE events SET actor_id = 'tamper' WHERE seq = 3")
        check = events.verify_chain(store.conn, "t1")
        assert not check.ok and check.first_bad_seq == 3

    def test_delete_detected(self, store):
        for i in range(4):
            append(store, task_id=f"t{i}")
        with store.transaction():
            store.run("DELETE FROM events WHERE seq = 2")
        check = events.verify_chain(store.conn, "t1")
        assert not check.ok


class TestListEvents:
    def test_filters(self, store):
        append(store, task_id="a", event_type="task_claimed")
        append(store, task_id="b", event_type="task_claimed")
        append(store, task_id="a", event_type="task_completed")
        assert len(events.list_events(store.conn, "t1")) == 3
        assert len(events.list_events(store.conn, "t1", task_id="a")) == 2
        assert len(events.list_events(store.conn, "t1",
                                      event_type="task_completed")) == 1
        assert events.list_events(store.conn, "t1", since_seq=2)[0]["seq"] == 3
        assert len(events.list_events(store.conn, "t1", limit=2)) == 2


class TestJsonlMirror:
    def test_mirror_shape(self, store, tmp_path):
        row = append(store, task_id="a", metadata={"round": 1})
        events.append_jsonl(tmp_path, row)
        lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["ts"] and record["type"] == "task_created"
        assert record["event_id"] == row["event_id"]
        assert record["round"] == 1  # metadata flattened in
