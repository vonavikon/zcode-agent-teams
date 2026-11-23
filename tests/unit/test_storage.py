"""Unit tests for agent_teams.storage."""
from __future__ import annotations

import sqlite3

import pytest

from agent_teams import storage
from agent_teams.errors import CorruptStateError, ExternalError
from agent_teams.storage import Store, open_store


@pytest.fixture
def store(tmp_path):
    with open_store(tmp_path) as s:
        with s.transaction():
            s.create_team({"id": "t1", "project_root": str(tmp_path)})
        yield s


class TestLifecycle:
    def test_open_idempotent(self, tmp_path):
        s = Store(tmp_path).open()
        assert s.open() is s
        s.close()
        s.close()  # idempotent

    def test_pragmas(self, tmp_path):
        with open_store(tmp_path) as s:
            def first_value(sql):
                row = s.one(sql)
                return next(iter(row.values()))
            assert first_value("PRAGMA journal_mode") == "wal"
            assert first_value("PRAGMA foreign_keys") == 1
            assert first_value("PRAGMA busy_timeout") == 10000

    def test_creates_missing_directory(self, tmp_path):
        with open_store(tmp_path / "deep" / "team") as s:
            assert s.path.exists()


class TestTransactions:
    def test_commit(self, store):
        with store.transaction():
            store.create_run({"id": "r1", "team_id": "t1", "number": 1,
                              "started_at": storage.now_iso(), "status": "active"})
        assert store.get_run("r1") is not None

    def test_rollback_on_exception(self, store):
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.create_run({"id": "r2", "team_id": "t1", "number": 2,
                                  "started_at": storage.now_iso(), "status": "active"})
                raise RuntimeError("x")
        assert store.get_run("r2") is None

    def test_nested_joins_outer(self, store):
        with pytest.raises(RuntimeError):
            with store.transaction():
                with store.transaction():
                    store.create_run({"id": "r3", "team_id": "t1", "number": 3,
                                      "started_at": storage.now_iso(),
                                      "status": "active"})
                raise RuntimeError("inner rollback rolls back everything")
        assert store.get_run("r3") is None

    def test_nested_outer_commit(self, store):
        with store.transaction():
            with store.transaction():
                store.create_run({"id": "r4", "team_id": "t1", "number": 4,
                                  "started_at": storage.now_iso(), "status": "active"})
        assert store.get_run("r4") is not None


class TestEntities:
    def test_task_roundtrip_with_json_columns(self, store):
        with store.transaction():
            store.upsert_task({"team_id": "t1", "id": "task-a", "title": "demo",
                               "assignee": "w1", "files_read": ["src/a.py"],
                               "files_write": ["out/b.md"], "acceptance": ["done"],
                               "deps": []})
        task = store.get_task("t1", "task-a")
        assert task["files_read"] == ["src/a.py"]
        assert task["files_write"] == ["out/b.md"]
        assert task["acceptance"] == ["done"]
        assert task["status"] == "pending"

    def test_update_task_sets_updated_at(self, store):
        with store.transaction():
            store.upsert_task({"team_id": "t1", "id": "u1", "title": "d",
                               "assignee": "w1"})
        before = store.get_task("t1", "u1")["updated_at"]
        with store.transaction():
            store.update_task("t1", "u1", status="in_progress")
        after = store.get_task("t1", "u1")
        assert after["status"] == "in_progress" and after["updated_at"] >= before

    def test_list_tasks_filter(self, store):
        with store.transaction():
            for tid in ("a", "b"):
                store.upsert_task({"team_id": "t1", "id": tid, "title": "d",
                                   "assignee": "w1"})
            store.update_task("t1", "a", status="completed")
        assert [t["id"] for t in store.list_tasks("t1", status="completed")] == ["a"]
        assert len(store.list_tasks("t1")) == 2

    def test_agents(self, store):
        with store.transaction():
            store.upsert_agent({"team_id": "t1", "id": "w1", "role": "executor"})
        assert store.get_agent("t1", "w1")["role"] == "executor"
        with store.transaction():
            store.upsert_agent({"team_id": "t1", "id": "w1", "status": "running"})
        assert store.get_agent("t1", "w1")["status"] == "running"
        assert len(store.list_agents("t1")) == 1

    def test_messages_and_mark_read(self, store):
        with store.transaction():
            store.add_message({"id": "m1", "team_id": "t1", "from_agent": "lead",
                               "to_agent": "w1", "text": "hi"})
            store.add_message({"id": "m2", "team_id": "t1", "from_agent": "v",
                               "to_agent": "w2", "text": "yo"})
        assert len(store.list_messages("t1", to_agent="w1")) == 1
        assert len(store.list_messages("t1", unread_only=True)) == 2
        with store.transaction():
            assert store.mark_read("t1", ["m1"], storage.now_iso()) == 1
            assert store.mark_read("t1", ["m1"], storage.now_iso()) == 0
        assert len(store.list_messages("t1", unread_only=True)) == 1

    def test_attempts_unique(self, store):
        with store.transaction():
            store.add_attempt({"id": "a1", "team_id": "t1", "task_id": "x",
                               "attempt": 1, "actor": "v", "verdict": "fail",
                               "report": {"criteria": []}})
        with pytest.raises(sqlite3.IntegrityError):
            with store.transaction():
                store.add_attempt({"id": "a2", "team_id": "t1", "task_id": "x",
                                   "attempt": 1, "actor": "v", "verdict": "pass",
                                   "report": {}})

    def test_usage_dedup(self, store):
        base = {"team_id": "t1", "run_id": "r", "agent_id": "w1",
                "total_tokens": 10, "source": "manual"}
        with store.transaction():
            assert store.add_usage({**base, "dedup_key": "k1"}) is not None
            assert store.add_usage({**base, "dedup_key": "k1"}) is None
            assert store.add_usage({**base, "dedup_key": None}) is not None
            assert store.add_usage({**base, "dedup_key": None}) is not None
        assert len(store.list_usage("t1")) == 3

    def test_exports_upsert(self, store):
        with store.transaction():
            first = store.upsert_export({"team_id": "t1", "run_id": "r",
                                         "target": "langfuse", "status": "pending"})
        assert first["status"] == "pending"
        with store.transaction():
            second = store.upsert_export({"team_id": "t1", "run_id": "r",
                                          "target": "langfuse", "status": "exported",
                                          "attempts": 1})
        assert second["status"] == "exported" and second["attempts"] == 1
        assert store.get_export("t1", "r", "langfuse")["id"] == first["id"]

    def test_foreign_keys_enforced(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            with store.transaction():
                store.upsert_task({"team_id": "ghost", "id": "x", "title": "d",
                                   "assignee": "w1"})

    def test_duplicate_team_rejected(self, tmp_path):
        with open_store(tmp_path) as s:
            with s.transaction():
                s.create_team({"id": "t1", "project_root": "."})
            with pytest.raises(sqlite3.IntegrityError):
                with s.transaction():
                    s.create_team({"id": "t1", "project_root": "."})


class TestBackup:
    def test_backup_contains_data_and_prunes(self, tmp_path):
        import shutil

        with open_store(tmp_path) as s:
            with s.transaction():
                s.create_team({"id": "t1", "project_root": "."})
                s.upsert_task({"team_id": "t1", "id": "a", "title": "d",
                               "assignee": "w1"})
            paths = [s.backup() for _ in range(12)]
            assert len(paths) == 12
            kept = sorted((tmp_path / "backups").glob("state-*.db"))
            assert len(kept) == 10
            # A backup restores: copy it in as a fresh team's state.db.
            restore_dir = tmp_path / "restore"
            restore_dir.mkdir()
            shutil.copy2(kept[-1], restore_dir / storage.DB_FILENAME)
            with Store(restore_dir).open() as check:
                assert check.get_task("t1", "a") is not None
