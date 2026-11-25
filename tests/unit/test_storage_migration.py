"""Migration behavior of the store."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from agent_teams import storage
from agent_teams.errors import CorruptStateError
from agent_teams.storage import Store

V3, V4, V5 = 3, 4, 5

# The columns version 4 adds and the value each one gives to a row that is
# older than the column (None is SQL NULL).
V4_COLUMNS = {
    "tasks": {"trusted_base_commit": None, "draft_checkpoint_commit": None,
              "dependency_base_json": "[]"},
    "submissions": {"evidence_status": "pending", "evidence_notes_json": "[]"},
}
# ... and how PRAGMA table_info declares them: type, NOT NULL flag, default.
V4_DECLARATION = {
    "tasks": {"trusted_base_commit": ("TEXT", 0, None),
              "draft_checkpoint_commit": ("TEXT", 0, None),
              "dependency_base_json": ("TEXT", 0, "'[]'")},
    "submissions": {"evidence_status": ("TEXT", 0, "'pending'"),
                    "evidence_notes_json": ("TEXT", 0, "'[]'")},
}
# What the engine will store in the two new JSON columns.
BASE = [{"task_id": "p1", "commit_sha": "abc123"}]
NOTES = [{"criterion_id": "AC-001", "path": "src/a.py", "line_start": 1,
          "line_end": 2}]


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


def _old_database(tmp_path, version):
    """A connection to a database with the schema and the stamp an engine of
    that schema version leaves behind."""
    conn = sqlite3.connect(tmp_path / storage.DB_FILENAME)
    for applied, script in storage.MIGRATIONS:
        if applied <= version:
            conn.executescript(script)
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                         (applied, f"2026-01-0{applied}T00:00:00.000Z"))
    conn.execute(f"PRAGMA user_version = {version}")
    return conn


def _insert(conn, table, **row):
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table}({columns}) VALUES ({marks})",
                 list(row.values()))


def _rows_of_schema_3(conn):
    """What an engine of schema 3 wrote: rows in every table it had."""
    _insert(conn, "teams", id="m1", project_root="/proj", base_commit="base000",
            config_json='{"workspace": "worktree"}', spec_json='{"tasks": [1, 2]}',
            created_at="2026-11-01T00:00:00.000Z", status="active",
            lead_token_hash="lead-hash")
    _insert(conn, "runs", id="r1", team_id="m1", number=1,
            started_at="2026-11-01T00:00:00.000Z", finished_at=None,
            status="active", metadata_json="{}",
            integration_base_commit=None, integration_commit_sha=None)
    _insert(conn, "agents", team_id="m1", id="a-eng", role="executor",
            status="idle", current_task="t2",
            started_at="2026-11-01T00:00:00.000Z", finished_at=None,
            heartbeat_at="2026-11-01T00:00:05.000Z", restarts=0,
            metadata_json='{"lens": "engine"}')
    common = dict(team_id="m1", assignee="a-eng", lens="engine",
                  files_read_json='["src/a.py"]',
                  files_write_json='["tests/unit/test_a.py"]', spec_json="{}",
                  created_at="2026-11-01T00:00:00.000Z",
                  updated_at="2026-11-01T00:00:03.000Z")
    _insert(conn, "tasks", **common,
            id="t1", title='caf\u00e9 "quoted"; done', deps_json="[]",
            acceptance_json='[{"id": "AC-001", "description": "done; really",'
                            ' "type": "manual"}]',
            status="completed", owner="a-eng", execution_token_hash=None,
            attempt=1, verification_failures=1, execution_retries=0, restarts=2,
            claimed_at="2026-11-01T00:00:01.000Z",
            submitted_at="2026-11-01T00:00:02.000Z",
            completed_at="2026-11-01T00:00:03.000Z",
            blocked_at=None, cancelled_at=None, blocked_reason="",
            last_event_ts="2026-11-01T00:00:03.000Z", lease_expires_at=None,
            heartbeat_at="2026-11-01T00:00:02.000Z", base_commit="base000",
            head_commit="head111", workspace_path="/ws/t1",
            branch="agent-teams/m1/t1/a1", tokens=42,
            metadata_json='{"changed_files": ["src/a.py"],'
                          ' "workspace_violations": []}',
            accepted_commit_sha="head111")
    _insert(conn, "tasks", **common,
            id="t2", title="open task", acceptance_json='["works"]',
            deps_json='["t1"]', status="in_progress", owner="a-eng",
            execution_token_hash="token-hash", attempt=2,
            verification_failures=0, execution_retries=1, restarts=0,
            claimed_at="2026-11-01T00:00:04.000Z", submitted_at=None,
            completed_at=None, blocked_at=None, cancelled_at=None,
            blocked_reason=None, last_event_ts="2026-11-01T00:00:04.000Z",
            lease_expires_at="2026-11-01T01:00:00.000Z",
            heartbeat_at="2026-11-01T00:00:05.000Z", base_commit="base000",
            head_commit=None, workspace_path="/ws/t2",
            branch="agent-teams/m1/t2/a2", tokens=7, metadata_json="{}",
            accepted_commit_sha=None)
    _insert(conn, "submissions", id="s1", team_id="m1", run_id="r1",
            task_id="t1", attempt=1, commit_sha="head111",
            base_commit_sha="base000", branch="agent-teams/m1/t1/a1",
            created_at="2026-11-01T00:00:02.000Z",
            changed_files_json='["src/a.py", "tests/unit/test_a.py"]',
            verification_status="passed", verifier="v-eng",
            report_json='{"verdict": "pass", "criteria":'
                        ' [{"criterion_id": "AC-001", "status": "pass"}]}')
    _insert(conn, "submissions", id="s2", team_id="m1", run_id="r1",
            task_id="t2", attempt=2, commit_sha="head222",
            base_commit_sha="base000", branch="agent-teams/m1/t2/a2",
            created_at="2026-11-01T00:00:06.000Z")
    _insert(conn, "messages", id="msg1", team_id="m1", run_id="r1",
            from_agent="lead", to_agent="a-eng", task_id="t1", kind="feedback",
            text="fix line 3; caf\u00e9",
            created_at="2026-11-01T00:00:03.000Z", read_at=None)
    _insert(conn, "verification_attempts", id="va1", team_id="m1", run_id="r1",
            task_id="t1", attempt=1, verifier="v-eng", actor="lead",
            verdict="fail", report_json="{}", feedback="line 3",
            created_at="2026-11-01T00:00:03.000Z")
    _insert(conn, "events", event_id="ev1", run_id="r1", team_id="m1",
            task_id="t1", actor_id="a-eng", event_type="task_submitted",
            timestamp="2026-11-01T00:00:02.000Z", metadata_json="{}",
            prev_hash="0" * 64, hash="f" * 64)
    _insert(conn, "token_usage", team_id="m1", run_id="r1", task_id="t1",
            agent_id="a-eng", model="model-x", provider="p", input_tokens=10,
            output_tokens=5, cache_read_tokens=0, cache_write_tokens=None,
            total_tokens=15, tool_uses=3, duration_ms=1200, cost_usd=0.0012,
            cost_quality="unknown", source="manual", dedup_key="usage-1",
            recorded_at="2026-11-01T00:00:03.000Z", raw_json="{}")
    _insert(conn, "exports", team_id="m1", run_id="r1", target="langfuse",
            trace_id=None, status="pending", attempts=0, last_error=None,
            payload_hash=None, exported_at=None)


def _v3_database_with_rows(tmp_path):
    """A team database as an engine of schema 3 left it."""
    conn = _old_database(tmp_path, V3)
    _rows_of_schema_3(conn)
    conn.commit()
    conn.close()
    return tmp_path / storage.DB_FILENAME


def _v4_database_with_rows(tmp_path):
    """A team database of schema 4 whose rows also use the new columns."""
    conn = _old_database(tmp_path, V4)
    _rows_of_schema_3(conn)
    conn.execute("UPDATE tasks SET trusted_base_commit = 'base000',"
                 " draft_checkpoint_commit = 'wip333',"
                 " dependency_base_json = ? WHERE id = 't1'", (json.dumps(BASE),))
    conn.execute("UPDATE submissions SET evidence_status = 'verified',"
                 " evidence_notes_json = ? WHERE id = 's1'", (json.dumps(NOTES),))
    conn.commit()
    conn.close()
    return tmp_path / storage.DB_FILENAME


def _statements_of(script):
    """The statements of a migration script with no semicolon in a string."""
    return [part for part in script.split(";") if part.strip()]


def _dump(path):
    """Every table of a database file: its columns (name, type, NOT NULL
    flag, default) in order and its rows as {column: value}."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        dump = {}
        for name in names:
            info = [(r[1], r[2], r[3], r[4])
                    for r in conn.execute(f"PRAGMA table_info({name})")]
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {name}")]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True,
                                                 default=repr))
            dump[name] = {"info": info, "rows": rows}
        return dump
    finally:
        conn.close()


def _as_schema_3(dump):
    """The dump without the columns of version 4 and its migration record."""
    out = {}
    for table, content in dump.items():
        rows = [{k: v for k, v in row.items()
                 if k not in V4_COLUMNS.get(table, ())} for row in content["rows"]]
        if table == "schema_migrations":
            rows = [row for row in rows if row["version"] <= V3]
        out[table] = rows
    return out


def _user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _schema(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
            " WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    finally:
        conn.close()


def _backup_names(tmp_path):
    backups = tmp_path / "backups"
    if not backups.exists():
        return []
    return sorted(p.name for p in backups.glob("state-*.db"))


def _columns(store, table):
    return [row[1] for row in store.conn.execute(f"PRAGMA table_info({table})")]


def _assert_now_at_v4(path, before):
    """The database went from `before` (a dump at schema 3) to schema 4,
    and nothing else changed."""
    after = _dump(path)
    assert _user_version(path) == V4 == storage.SCHEMA_VERSION
    assert list(after) == list(before)  # no table added, dropped or renamed
    for table, content in before.items():
        kept = content["info"]
        # every column keeps its name, type, constraint, default and place
        assert after[table]["info"][:len(kept)] == kept
        added = after[table]["info"][len(kept):]
        assert added == [(name, *V4_DECLARATION[table][name])
                         for name in V4_COLUMNS.get(table, ())]
    assert [row["version"] for row in after["schema_migrations"]["rows"]] == [1, 2, 3, 4]
    assert _as_schema_3(after) == _as_schema_3(before)  # every row and value
    for table, added in V4_COLUMNS.items():
        for row in after[table]["rows"]:  # old rows read the defaults
            assert {name: row[name] for name in added} == added


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

    def test_v3_with_rows_opens_at_v4(self, tmp_path):
        path = _v3_database_with_rows(tmp_path)
        before = _dump(path)
        assert [row["version"] for row in before["schema_migrations"]["rows"]] == [1, 2, 3]
        for table, added in V4_COLUMNS.items():
            assert not added.keys() & {c[0] for c in before[table]["info"]}

        with Store(tmp_path).open():
            pass

        _assert_now_at_v4(path, before)
        backups = sorted((tmp_path / "backups").glob("state-*.db"))
        assert len(backups) == 1
        # The backup taken before the migration is the database as it was.
        restored = tmp_path / "restored"
        restored.mkdir()
        shutil.copy2(backups[0], restored / storage.DB_FILENAME)
        assert _user_version(restored / storage.DB_FILENAME) == V3
        assert _dump(restored / storage.DB_FILENAME) == before

    def test_migrated_rows_take_the_new_fields(self, tmp_path):
        path = _v3_database_with_rows(tmp_path)
        with Store(tmp_path).open():
            pass
        with Store(tmp_path).open() as s:
            task = s.get_task("m1", "t2")
            assert (task["dependency_base"], task["trusted_base_commit"],
                    task["draft_checkpoint_commit"]) == ([], None, None)
            submission = s.get_submission("s2")
            assert (submission["evidence_status"],
                    submission["evidence_notes"]) == ("pending", [])
            with s.transaction():
                s.update_task("m1", "t2", dependency_base=BASE,
                              trusted_base_commit="base000",
                              draft_checkpoint_commit="wip333")
                s.update_submission("s2", verification_status="passed",
                                    evidence_status="verified",
                                    evidence_notes=NOTES)
            task = s.get_task("m1", "t2")
            assert task["dependency_base"] == BASE
            assert (task["trusted_base_commit"],
                    task["draft_checkpoint_commit"]) == ("base000", "wip333")
            # the values the row had before are still there
            assert (task["title"], task["tokens"], task["deps"]) == (
                "open task", 7, ["t1"])
            submission = s.get_submission("s2")
            assert (submission["evidence_status"],
                    submission["evidence_notes"]) == ("verified", NOTES)
            assert submission["verification_status"] == "passed"
            # `dependency_base=` and `evidence_notes=` land in the *_json columns
            stored = s.conn.execute("SELECT dependency_base_json FROM tasks"
                                    " WHERE id = 't2'").fetchone()[0]
            assert stored == json.dumps(BASE, ensure_ascii=False)
            stored = s.conn.execute("SELECT evidence_notes_json FROM submissions"
                                    " WHERE id = 's2'").fetchone()[0]
            assert stored == json.dumps(NOTES, ensure_ascii=False)
            # the other rows were not touched
            assert s.get_task("m1", "t1")["dependency_base"] == []
            assert s.get_submission("s1")["evidence_status"] == "pending"
            assert [t["id"] for t in s.list_tasks("m1")] == ["t1", "t2"]
            assert s.latest_submission("m1", "t1")["id"] == "s1"

    def test_rows_written_without_the_new_columns_get_the_defaults(self, tmp_path):
        """The helpers that predate version 4 insert without its columns."""
        with Store(tmp_path).open() as s:
            with s.transaction():
                s.create_team({"id": "t1", "project_root": "."})
                s.upsert_task({"team_id": "t1", "id": "a", "title": "d",
                               "assignee": "w1"})
                s.add_submission({"id": "s1", "team_id": "t1", "task_id": "a",
                                  "attempt": 1, "commit_sha": "abc"})
            task = s.get_task("t1", "a")
            assert (task["dependency_base"], task["trusted_base_commit"],
                    task["draft_checkpoint_commit"]) == ([], None, None)
            submission = s.get_submission("s1")
            assert (submission["evidence_status"],
                    submission["evidence_notes"]) == ("pending", [])
            with s.transaction():
                s.update_task("t1", "a", dependency_base=BASE,
                              trusted_base_commit="base9",
                              draft_checkpoint_commit="wip42")
                s.update_submission("s1", evidence_status="verified",
                                    evidence_notes=NOTES)
            task = s.get_task("t1", "a")
            assert (task["dependency_base"], task["trusted_base_commit"],
                    task["draft_checkpoint_commit"]) == (BASE, "base9", "wip42")
            submission = s.get_submission("s1")
            assert (submission["evidence_status"],
                    submission["evidence_notes"]) == ("verified", NOTES)
            # `dependency_base=` and `evidence_notes=` land in the *_json columns
            stored = s.conn.execute("SELECT dependency_base_json FROM tasks"
                                    " WHERE id = 'a'").fetchone()[0]
            assert stored == json.dumps(BASE, ensure_ascii=False)
            stored = s.conn.execute("SELECT evidence_notes_json FROM submissions"
                                    " WHERE id = 's1'").fetchone()[0]
            assert stored == json.dumps(NOTES, ensure_ascii=False)

    @pytest.mark.parametrize("applied", range(1, 6))
    def test_v4_finishes_after_a_crash_inside_it(self, tmp_path, applied):
        """A crash between statements and the version stamp: the retry
        skips the columns that made it and adds the rest."""
        path = _v3_database_with_rows(tmp_path)
        before = _dump(path)
        statements = _statements_of(storage._MIGRATION_V4)
        assert len(statements) == sum(len(added) for added in V4_COLUMNS.values())
        conn = sqlite3.connect(path)
        for statement in statements[:applied]:
            conn.execute(statement)
        conn.commit()
        conn.close()  # the stamp is still 3
        with Store(tmp_path).open():
            pass
        _assert_now_at_v4(path, before)

    @pytest.mark.parametrize("applied", range(1, 6))
    def test_v3_finishes_after_a_crash_inside_it(self, tmp_path, applied):
        """The same for the older migration, which mixes CREATE IF NOT
        EXISTS with three ALTERs."""
        conn = _old_database(tmp_path, 2)
        _insert(conn, "teams", id="m1", project_root="/proj", config_json="{}",
                spec_json="{}", created_at="t")
        _insert(conn, "tasks", team_id="m1", id="t1", title="d", assignee="a",
                files_read_json="[]", files_write_json="[]", acceptance_json="[]",
                deps_json="[]", spec_json="{}", status="pending", created_at="t",
                updated_at="t")
        _insert(conn, "runs", id="r1", team_id="m1", number=1, started_at="t",
                status="active")
        for statement in _statements_of(storage._MIGRATION_V3)[:applied]:
            conn.execute(statement)
        conn.commit()
        conn.close()  # the stamp is still 2
        with Store(tmp_path).open() as s:
            assert s.one("PRAGMA user_version")["user_version"] == V4
            assert _columns(s, "runs")[-2:] == ["integration_base_commit",
                                                "integration_commit_sha"]
            assert "accepted_commit_sha" in _columns(s, "tasks")
            assert s.get_task("m1", "t1")["title"] == "d"
            assert s.get_run("r1") is not None
            assert s.one("SELECT count(*) AS n FROM submissions")["n"] == 0

    def test_reopening_a_migrated_database_changes_nothing(self, tmp_path):
        path = _v3_database_with_rows(tmp_path)
        with Store(tmp_path).open():
            pass
        migrated, schema, backups = _dump(path), _schema(path), _backup_names(tmp_path)
        with Store(tmp_path).open() as s:
            assert s.one("PRAGMA user_version")["user_version"] == V4
        assert _dump(path) == migrated
        assert _schema(path) == schema
        assert _backup_names(tmp_path) == backups

    def test_v4_database_is_accepted_as_it_is(self, tmp_path):
        path = _v4_database_with_rows(tmp_path)
        before = _dump(path)
        with Store(tmp_path).open() as s:
            assert s.one("PRAGMA user_version")["user_version"] == V4
            assert s.get_task("m1", "t1")["dependency_base"] == BASE
            assert s.get_submission("s1")["evidence_notes"] == NOTES
        assert _dump(path) == before
        assert _backup_names(tmp_path) == []

    def test_v5_rejected_and_left_alone(self, tmp_path):
        path = _v4_database_with_rows(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute(f"PRAGMA user_version = {V5}")
        conn.commit()
        conn.close()
        before = _dump(path)
        with pytest.raises(CorruptStateError, match="newer engine"):
            Store(tmp_path).open()
        assert _user_version(path) == V5
        assert _dump(path) == before
        assert _backup_names(tmp_path) == []

    def test_any_other_error_fails_the_migration(self, tmp_path, monkeypatch):
        """A duplicate column is skipped, the statements after it still
        run, and any other error still fails the migration."""
        bad = ("ALTER TABLE teams ADD COLUMN project_root TEXT;"
               " ALTER TABLE teams ADD COLUMN note TEXT;"
               " ALTER TABLE teams ADD COLUMN broken TEXT UNIQUE;")
        with monkeypatch.context() as patched:
            patched.setattr(storage, "MIGRATIONS", [*storage.MIGRATIONS, (V5, bad)])
            with pytest.raises(CorruptStateError, match="cannot open"):
                Store(tmp_path).open()
        with Store(tmp_path).open() as s:  # the engine that knows schema 4
            assert _user_version(tmp_path / storage.DB_FILENAME) == V4
            assert "note" in _columns(s, "teams")
            assert "broken" not in _columns(s, "teams")


class TestStatementSplit:
    """A migration script is applied one statement at a time."""

    @pytest.mark.parametrize("script, expected", [
        ("", []),
        ("SELECT 1;", ["SELECT 1;"]),
        ("SELECT 1;\nSELECT 2", ["SELECT 1;", "\nSELECT 2"]),
        ("SELECT 1;\n-- done\n", ["SELECT 1;", "\n-- done\n"]),
        ("CREATE TABLE t(s TEXT DEFAULT 'a;b');\nALTER TABLE t ADD COLUMN c"
         " TEXT;", ["CREATE TABLE t(s TEXT DEFAULT 'a;b');",
                    "\nALTER TABLE t ADD COLUMN c TEXT;"]),
        ("-- note; not a cut\nALTER TABLE t ADD COLUMN c TEXT;",
         ["-- note; not a cut\nALTER TABLE t ADD COLUMN c TEXT;"]),
        ("CREATE TRIGGER tr BEFORE INSERT ON t BEGIN SELECT 1; END;\nDELETE"
         " FROM t;", ["CREATE TRIGGER tr BEFORE INSERT ON t BEGIN SELECT 1;"
                      " END;", "\nDELETE FROM t;"]),
    ])
    def test_cuts_only_at_the_end_of_a_statement(self, script, expected):
        parts = storage._split_statements(script)
        assert parts == expected
        assert "".join(parts) == script
