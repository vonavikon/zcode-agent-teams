"""SQLite-backed state storage: one database per team directory.

Replaces the v0.2 JSON files as the source of truth (spec section 3.4).
WAL mode, IMMEDIATE transactions, foreign keys and a per-team schema
version with migrations and backups. JSON columns (``*_json``) are passed
and returned as objects, never as strings. Timestamps everywhere are
:func:`now_iso` strings.

Public API::

    Store(team_dir).open() / .close(); store.transaction(); store.one/all/run
    entity helpers: create_team/get_team/update_team, create_run/get_run/
    update_run, upsert_task/get_task/list_tasks/update_task, upsert_agent/
    get_agent/list_agents/update_agent, add_message/list_messages/mark_read,
    add_attempt/list_attempts, add_usage/list_usage, upsert_export/get_export
    backup(); open_store(team_dir)
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import CorruptStateError, ExternalError

SCHEMA_VERSION = 3
DB_FILENAME = "state.db"

_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams(
  id TEXT PRIMARY KEY,
  project_root TEXT NOT NULL,
  base_commit TEXT,
  config_json TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL REFERENCES teams(id),
  number INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(team_id, number)
);
CREATE TABLE IF NOT EXISTS agents(
  team_id TEXT NOT NULL REFERENCES teams(id),
  id TEXT NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  current_task TEXT,
  started_at TEXT,
  finished_at TEXT,
  heartbeat_at TEXT,
  restarts INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (team_id, id)
);
CREATE TABLE IF NOT EXISTS tasks(
  team_id TEXT NOT NULL REFERENCES teams(id),
  id TEXT NOT NULL,
  title TEXT NOT NULL,
  assignee TEXT NOT NULL,
  lens TEXT NOT NULL DEFAULT '',
  files_read_json TEXT NOT NULL,
  files_write_json TEXT NOT NULL,
  acceptance_json TEXT NOT NULL,
  deps_json TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL,
  owner TEXT,
  execution_token_hash TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  verification_failures INTEGER NOT NULL DEFAULT 0,
  execution_retries INTEGER NOT NULL DEFAULT 0,
  restarts INTEGER NOT NULL DEFAULT 0,
  claimed_at TEXT,
  submitted_at TEXT,
  completed_at TEXT,
  blocked_at TEXT,
  cancelled_at TEXT,
  blocked_reason TEXT,
  last_event_ts TEXT,
  lease_expires_at TEXT,
  heartbeat_at TEXT,
  base_commit TEXT,
  head_commit TEXT,
  workspace_path TEXT,
  branch TEXT,
  tokens INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (team_id, id)
);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL REFERENCES teams(id),
  run_id TEXT,
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  task_id TEXT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  read_at TEXT
);
CREATE TABLE IF NOT EXISTS verification_attempts(
  id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL REFERENCES teams(id),
  run_id TEXT,
  task_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  verifier TEXT,
  actor TEXT NOT NULL,
  verdict TEXT NOT NULL,
  report_json TEXT NOT NULL,
  feedback TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (team_id, task_id, attempt)
);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL,
  team_id TEXT NOT NULL REFERENCES teams(id),
  task_id TEXT,
  actor_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id TEXT NOT NULL REFERENCES teams(id),
  run_id TEXT NOT NULL,
  task_id TEXT,
  agent_id TEXT NOT NULL,
  model TEXT,
  provider TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER,
  total_tokens INTEGER,
  tool_uses INTEGER,
  duration_ms INTEGER,
  cost_usd REAL,
  cost_quality TEXT NOT NULL DEFAULT 'unknown',
  source TEXT NOT NULL,
  dedup_key TEXT,
  recorded_at TEXT NOT NULL,
  raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS exports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id TEXT NOT NULL REFERENCES teams(id),
  run_id TEXT NOT NULL,
  target TEXT NOT NULL,
  trace_id TEXT,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  payload_hash TEXT,
  exported_at TEXT,
  UNIQUE (team_id, run_id, target)
);
CREATE INDEX IF NOT EXISTS idx_events_team_run_seq ON events(team_id, run_id, seq);
CREATE INDEX IF NOT EXISTS idx_tasks_team_status ON tasks(team_id, status);
CREATE INDEX IF NOT EXISTS idx_messages_team_to_read ON messages(team_id, to_agent, read_at);
CREATE INDEX IF NOT EXISTS idx_attempts_team_task ON verification_attempts(team_id, task_id);
CREATE INDEX IF NOT EXISTS idx_usage_team_task ON token_usage(team_id, task_id);
CREATE INDEX IF NOT EXISTS idx_usage_team_run ON token_usage(team_id, run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_team_dedup ON token_usage(team_id, dedup_key);
"""

_MIGRATION_V2 = """
ALTER TABLE teams ADD COLUMN lead_token_hash TEXT;
"""

_MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS submissions(
  id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL REFERENCES teams(id),
  run_id TEXT,
  task_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  commit_sha TEXT NOT NULL,
  base_commit_sha TEXT,
  branch TEXT,
  created_at TEXT NOT NULL,
  changed_files_json TEXT NOT NULL DEFAULT '[]',
  verification_status TEXT NOT NULL DEFAULT 'pending',
  verifier TEXT,
  report_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_submissions_team_task
  ON submissions(team_id, task_id, created_at);
ALTER TABLE tasks ADD COLUMN accepted_commit_sha TEXT;
ALTER TABLE runs ADD COLUMN integration_base_commit TEXT;
ALTER TABLE runs ADD COLUMN integration_commit_sha TEXT;
"""

MIGRATIONS: list[tuple[int, str]] = [(1, _MIGRATION_V1), (2, _MIGRATION_V2),
                                     (3, _MIGRATION_V3)]

_JSON_SUFFIX = "_json"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def now_iso() -> str:
    """The package-wide UTC timestamp: ISO 8601, milliseconds, Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _decode(row: sqlite3.Row) -> dict:
    out = {}
    for key in row.keys():
        value = row[key]
        if key.endswith(_JSON_SUFFIX):
            # files_read_json -> files_read, decoded to an object
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = None  # a corrupt column surfaces as None, not a crash
            key = key[: -len(_JSON_SUFFIX)]
        out[key] = value
    return out


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class Store:
    """Owns the sqlite3 connection of one team's state.db."""

    def __init__(self, team_dir: Path):
        self.team_dir = Path(team_dir)
        self.path = self.team_dir / DB_FILENAME
        self.conn: sqlite3.Connection | None = None
        self._tx_depth = 0

    # -- lifecycle ------------------------------------------------------

    def open(self) -> "Store":
        if self.conn is not None:
            return self
        self.team_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(str(self.path), isolation_level=None,
                                        timeout=10.0)
            self.conn.row_factory = sqlite3.Row
            for pragma in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=FULL",
                           "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=10000"):
                self.conn.execute(pragma)
            self._migrate()
            check = self.conn.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise CorruptStateError(
                    f"integrity check failed for {self.path}: {check[0] if check else 'no result'}")
        except CorruptStateError:
            self.close()
            raise
        except sqlite3.Error as exc:
            self.close()
            raise CorruptStateError(f"cannot open {self.path}: {exc}") from exc
        return self

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass  # checkpoint is best effort; close must never fail
        try:
            self.conn.close()
        finally:
            self.conn = None

    def __enter__(self) -> "Store":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise CorruptStateError(
                f"database {self.path} was created by a newer engine version "
                f"(schema {version}, this engine supports {SCHEMA_VERSION})")
        for target, sql in MIGRATIONS:
            if target <= version:
                continue
            self.backup()
            # executescript() commits any open transaction by itself, so the
            # script runs outside transaction() and is idempotent instead.
            try:
                self.conn.executescript(sql)
            except sqlite3.OperationalError as exc:
                # ALTER TABLE ... ADD COLUMN cannot use IF NOT EXISTS. A crash
                # between the ALTER and the user_version update would leave the
                # column in place; that state is safe to finish, not an error.
                if "duplicate column" not in str(exc).lower():
                    raise
            with self.transaction():
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (target, now_iso()))
                self.conn.execute(f"PRAGMA user_version = {target}")

    # -- queries --------------------------------------------------------

    @contextmanager
    def transaction(self):
        if self.conn is None:
            raise ExternalError("store is not open")
        if self._tx_depth > 0:
            self._tx_depth += 1  # nested call joins the outer transaction
            try:
                yield self.conn
            finally:
                self._tx_depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            yield self.conn
        except BaseException:
            self._tx_depth = 0
            self.conn.execute("ROLLBACK")
            raise
        self._tx_depth = 0
        self.conn.execute("COMMIT")

    def one(self, sql: str, params=()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return _decode(row) if row is not None else None

    def all(self, sql: str, params=()) -> list[dict]:
        return [_decode(row) for row in self.conn.execute(sql, params).fetchall()]

    def run(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    # -- entities -------------------------------------------------------

    def create_team(self, team: dict) -> None:
        self.run(
            "INSERT INTO teams(id, project_root, base_commit, config_json, spec_json,"
            " created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team["id"], team["project_root"], team.get("base_commit"),
             _encode(team.get("config", {})), _encode(team.get("spec", {})),
             team.get("created_at", now_iso()), team.get("status", "active")))

    def get_team(self, team_id: str) -> dict | None:
        return self.one("SELECT * FROM teams WHERE id = ?", (team_id,))

    def update_team(self, team_id: str, **fields) -> None:
        self._update("teams", {"id": team_id}, fields)

    def create_run(self, run: dict) -> None:
        self.run(
            "INSERT INTO runs(id, team_id, number, started_at, finished_at, status,"
            " metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run["id"], run["team_id"], run["number"], run["started_at"],
             run.get("finished_at"), run["status"], _encode(run.get("metadata", {}))))

    def get_run(self, run_id: str) -> dict | None:
        return self.one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def update_run(self, run_id: str, **fields) -> None:
        self._update("runs", {"id": run_id}, fields)

    def upsert_task(self, task: dict) -> None:
        ts = task.get("created_at", now_iso())
        self.run(
            "INSERT INTO tasks(team_id, id, title, assignee, lens, files_read_json,"
            " files_write_json, acceptance_json, deps_json, spec_json, status,"
            " attempt, verification_failures, execution_retries, restarts, tokens,"
            " metadata_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(team_id, id) DO UPDATE SET title=excluded.title,"
            " assignee=excluded.assignee, lens=excluded.lens,"
            " files_read_json=excluded.files_read_json,"
            " files_write_json=excluded.files_write_json,"
            " acceptance_json=excluded.acceptance_json, deps_json=excluded.deps_json,"
            " spec_json=excluded.spec_json, metadata_json=excluded.metadata_json,"
            " updated_at=excluded.updated_at",
            (task["team_id"], task["id"], task["title"], task["assignee"],
             task.get("lens", ""), _encode(task.get("files_read", [])),
             _encode(task.get("files_write", [])), _encode(task.get("acceptance", [])),
             _encode(task.get("deps", [])), _encode(task.get("spec", task)),
             task.get("status", "pending"), task.get("attempt", 0),
             task.get("verification_failures", 0), task.get("execution_retries", 0),
             task.get("restarts", 0), task.get("tokens", 0),
             _encode(task.get("metadata", {})), ts, ts))

    def get_task(self, team_id: str, task_id: str) -> dict | None:
        return self.one("SELECT * FROM tasks WHERE team_id = ? AND id = ?",
                        (team_id, task_id))

    def list_tasks(self, team_id: str, status: str | None = None) -> list[dict]:
        if status:
            return self.all("SELECT * FROM tasks WHERE team_id = ? AND status = ?"
                            " ORDER BY id", (team_id, status))
        return self.all("SELECT * FROM tasks WHERE team_id = ? ORDER BY id", (team_id,))

    def update_task(self, team_id: str, task_id: str, **fields) -> None:
        fields.setdefault("updated_at", now_iso())
        self._update("tasks", {"team_id": team_id, "id": task_id}, fields)

    def upsert_agent(self, agent: dict) -> None:
        self.run(
            "INSERT INTO agents(team_id, id, role, status, current_task, started_at,"
            " finished_at, heartbeat_at, restarts, metadata_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(team_id, id) DO UPDATE SET role=excluded.role,"
            " status=excluded.status, current_task=excluded.current_task,"
            " finished_at=excluded.finished_at,"
            " heartbeat_at=excluded.heartbeat_at, restarts=excluded.restarts,"
            " metadata_json=excluded.metadata_json",
            (agent["team_id"], agent["id"], agent.get("role", "executor"),
             agent.get("status", "idle"), agent.get("current_task"),
             agent.get("started_at"), agent.get("finished_at"),
             agent.get("heartbeat_at"), agent.get("restarts", 0),
             _encode(agent.get("metadata", {}))))

    def get_agent(self, team_id: str, agent_id: str) -> dict | None:
        return self.one("SELECT * FROM agents WHERE team_id = ? AND id = ?",
                        (team_id, agent_id))

    def list_agents(self, team_id: str) -> list[dict]:
        return self.all("SELECT * FROM agents WHERE team_id = ? ORDER BY id", (team_id,))

    def update_agent(self, team_id: str, agent_id: str, **fields) -> None:
        self._update("agents", {"team_id": team_id, "id": agent_id}, fields)

    def add_message(self, message: dict) -> dict:
        message = {**message, "created_at": message.get("created_at", now_iso())}
        self.run(
            "INSERT INTO messages(id, team_id, run_id, from_agent, to_agent, task_id,"
            " kind, text, created_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message["id"], message["team_id"], message.get("run_id"),
             message["from_agent"], message["to_agent"], message.get("task_id"),
             message.get("kind", "message"), message["text"], message["created_at"],
             message.get("read_at")))
        return message

    def list_messages(self, team_id: str, to_agent: str | None = None,
                      unread_only: bool = False) -> list[dict]:
        sql, params = "SELECT * FROM messages WHERE team_id = ?", [team_id]
        if to_agent is not None:
            sql += " AND to_agent = ?"
            params.append(to_agent)
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY created_at, id"
        return self.all(sql, params)

    def mark_read(self, team_id: str, message_ids: list[str], read_at: str) -> int:
        changed = 0
        for mid in message_ids:
            cursor = self.run(
                "UPDATE messages SET read_at = ? WHERE team_id = ? AND id = ?"
                " AND read_at IS NULL", (read_at, team_id, mid))
            changed += cursor.rowcount
        return changed

    def add_submission(self, submission: dict) -> dict:
        submission = {**submission,
                      "created_at": submission.get("created_at", now_iso())}
        self.run(
            "INSERT INTO submissions(id, team_id, run_id, task_id, attempt,"
            " commit_sha, base_commit_sha, branch, created_at,"
            " changed_files_json, verification_status, verifier, report_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (submission["id"], submission["team_id"], submission.get("run_id"),
             submission["task_id"], submission["attempt"],
             submission["commit_sha"], submission.get("base_commit_sha"),
             submission.get("branch"), submission["created_at"],
             _encode(submission.get("changed_files", [])),
             submission.get("verification_status", "pending"),
             submission.get("verifier"),
             _encode(submission.get("report", {}))))
        return submission

    def get_submission(self, submission_id: str) -> dict | None:
        return self.one("SELECT * FROM submissions WHERE id = ?", (submission_id,))

    def latest_submission(self, team_id: str,
                          task_id: str) -> dict | None:
        return self.one(
            "SELECT * FROM submissions WHERE team_id = ? AND task_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1", (team_id, task_id))

    def update_submission(self, submission_id: str, **fields) -> None:
        self._update("submissions", {"id": submission_id}, fields)

    def list_submissions(self, team_id: str,
                         task_id: str | None = None) -> list[dict]:
        if task_id is None:
            return self.all("SELECT * FROM submissions WHERE team_id = ?"
                            " ORDER BY created_at, id", (team_id,))
        return self.all("SELECT * FROM submissions WHERE team_id = ? AND"
                        " task_id = ? ORDER BY created_at, id",
                        (team_id, task_id))

    def add_attempt(self, attempt: dict) -> dict:
        attempt = {**attempt, "created_at": attempt.get("created_at", now_iso())}
        self.run(
            "INSERT INTO verification_attempts(id, team_id, run_id, task_id, attempt,"
            " verifier, actor, verdict, report_json, feedback, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (attempt["id"], attempt["team_id"], attempt.get("run_id"),
             attempt["task_id"], attempt["attempt"], attempt.get("verifier"),
             attempt["actor"], attempt["verdict"], _encode(attempt.get("report", {})),
             attempt.get("feedback", ""), attempt["created_at"]))
        return attempt

    def list_attempts(self, team_id: str, task_id: str) -> list[dict]:
        return self.all(
            "SELECT * FROM verification_attempts WHERE team_id = ? AND task_id = ?"
            " ORDER BY attempt", (team_id, task_id))

    def add_usage(self, record: dict) -> int | None:
        if record.get("dedup_key") is not None:
            exists = self.one(
                "SELECT id FROM token_usage WHERE team_id = ? AND dedup_key = ?",
                (record["team_id"], record["dedup_key"]))
            if exists:
                return None
        cursor = self.run(
            "INSERT INTO token_usage(team_id, run_id, task_id, agent_id, model,"
            " provider, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens, total_tokens, tool_uses, duration_ms, cost_usd,"
            " cost_quality, source, dedup_key, recorded_at, raw_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["team_id"], record["run_id"], record.get("task_id"),
             record["agent_id"], record.get("model"), record.get("provider"),
             record.get("input_tokens"), record.get("output_tokens"),
             record.get("cache_read_tokens"), record.get("cache_write_tokens"),
             record.get("total_tokens"), record.get("tool_uses"),
             record.get("duration_ms"), record.get("cost_usd"),
             record.get("cost_quality", "unknown"), record.get("source", "manual"),
             record.get("dedup_key"), record.get("recorded_at", now_iso()),
             _encode(record.get("raw", {}))))
        return cursor.lastrowid

    def list_usage(self, team_id: str, run_id: str | None = None,
                   task_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM token_usage WHERE team_id = ?", [team_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        if task_id is not None:
            sql += " AND task_id = ?"
            params.append(task_id)
        return self.all(sql + " ORDER BY recorded_at, id", params)

    def upsert_export(self, record: dict) -> dict:
        existing = self.get_export(record["team_id"], record["run_id"],
                                   record["target"])
        if existing is None:
            self.run(
                "INSERT INTO exports(team_id, run_id, target, trace_id, status,"
                " attempts, last_error, payload_hash, exported_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["team_id"], record["run_id"], record["target"],
                 record.get("trace_id"), record.get("status", "pending"),
                 record.get("attempts", 0), record.get("last_error"),
                 record.get("payload_hash"), record.get("exported_at")))
            return self.get_export(record["team_id"], record["run_id"], record["target"])
        sets, params = [], []
        for key in ("trace_id", "status", "attempts", "last_error", "payload_hash",
                    "exported_at"):
            if key in record:
                sets.append(f"{key} = ?")
                params.append(record[key])
        if sets:
            params.extend([record["team_id"], record["run_id"], record["target"]])
            self.run(f"UPDATE exports SET {', '.join(sets)} "
                     f"WHERE team_id = ? AND run_id = ? AND target = ?", params)
        return self.get_export(record["team_id"], record["run_id"], record["target"])

    def get_export(self, team_id: str, run_id: str, target: str) -> dict | None:
        return self.one("SELECT * FROM exports WHERE team_id = ? AND run_id = ?"
                        " AND target = ?", (team_id, run_id, target))

    # -- backup ---------------------------------------------------------

    def backup(self) -> Path:
        backups = self.team_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = now_iso().replace("-", "").replace(":", "").replace(".", "").replace("Z", "Z")
        target = backups / f"state-{stamp}.db"
        destination = sqlite3.connect(str(target))
        try:
            if self.conn is not None:
                self.conn.backup(destination)
            else:
                source = sqlite3.connect(str(self.path))
                try:
                    source.backup(destination)
                finally:
                    source.close()
        finally:
            destination.close()
        kept = sorted(backups.glob("state-*.db"))
        for old in kept[:-10]:
            try:
                old.unlink()
            except OSError:
                pass
        return target

    # -- internals ------------------------------------------------------

    _table_columns: dict[str, frozenset[str]] = {}

    def _columns_of(self, table: str) -> frozenset[str]:
        cached = Store._table_columns.get(table)
        if cached is None:
            rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            cached = frozenset(row[1] for row in rows)
            Store._table_columns[table] = cached
        return cached

    def _update(self, table: str, key: dict, fields: dict) -> None:
        if not _SAFE_NAME.match(table):
            raise ExternalError(f"bad table name {table!r}")
        if not fields:
            return
        real_columns = self._columns_of(table)
        resolved: dict[str, tuple[str, object]] = {}
        for name, value in fields.items():
            if not _SAFE_NAME.match(name):
                raise ExternalError(f"bad column name {name!r} for {table}")
            if name in real_columns:
                resolved[name] = (name, value)
            elif f"{name}{_JSON_SUFFIX}" in real_columns:
                # Callers may say `metadata=`; the column is `metadata_json`.
                resolved[name] = (f"{name}{_JSON_SUFFIX}", _encode(value))
            else:
                raise ExternalError(f"unknown column {name!r} for {table}")
        assignments = ", ".join(f"{col} = ?" for col, _ in resolved.values())
        values = [value for _, value in resolved.values()]
        where = " AND ".join(f"{name} = ?" for name in key)
        self.run(f"UPDATE {table} SET {assignments} WHERE {where}",
                 [*values, *key.values()])


def open_store(team_dir: Path) -> Store:
    return Store(team_dir).open()
