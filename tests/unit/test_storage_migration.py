"""Migration behavior of the store."""
from __future__ import annotations

import sqlite3

import pytest

from agent_teams import storage
from agent_teams.errors import CorruptStateError
from agent_teams.storage import Store


def _fresh_db_with_version(tmp_path, version, create_tables=False):
    path = tmp_path / storage.DB_FILENAME
    conn = sqlite3.connect(path)
    if create_tables:
        conn.executescript(storage._MIGRATION_V1)
        conn.execute(f"PRAGMA user_version = {version}")
    else:
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    # A file-based version bump left no WAL side files to worry about.
    return path


class TestMigrations:
    def test_new_file_gets_schema_version(self, tmp_path):
        with Store(tmp_path).open() as s:
            assert s.one("PRAGMA user_version")["user_version"] == storage.SCHEMA_VERSION
            tables = {r["name"] for r in s.all(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert {"teams", "tasks", "agents", "messages", "events",
                    "verification_attempts", "token_usage", "exports",
                    "schema_migrations", "runs"} <= tables

    def test_reopen_applies_nothing_twice(self, tmp_path):
        with Store(tmp_path).open() as first:
            applied = first.all("SELECT version FROM schema_migrations")
        with Store(tmp_path).open() as second:
            assert second.all("SELECT version FROM schema_migrations") == applied

    def test_v0_migrated_with_backup(self, tmp_path):
        _fresh_db_with_version(tmp_path, 0)
        with Store(tmp_path).open() as s:
            assert s.one("PRAGMA user_version")["user_version"] == storage.SCHEMA_VERSION
        assert any((tmp_path / "backups").glob("state-*.db"))

    def test_newer_version_rejected(self, tmp_path):
        _fresh_db_with_version(tmp_path, storage.SCHEMA_VERSION + 1, create_tables=False)
        with pytest.raises(CorruptStateError, match="newer engine"):
            Store(tmp_path).open()

    def test_corrupt_file_rejected(self, tmp_path):
        (tmp_path / storage.DB_FILENAME).write_bytes(b"this is not a database" * 100)
        with pytest.raises(CorruptStateError):
            Store(tmp_path).open()
