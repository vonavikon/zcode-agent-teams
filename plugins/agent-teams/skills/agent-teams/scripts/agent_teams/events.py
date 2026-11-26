"""Append-only event log with a tamper-evident hash chain (spec 3.5).

Every meaningful engine action becomes one row in ``events`` inside the
caller's open transaction. Each row's ``hash`` covers the previous row's
hash plus the canonical JSON of the row itself, so any later edit of a
stored row breaks :func:`verify_chain` at exactly that ``seq``. A JSONL
mirror (``events.jsonl``) keeps the v0.2 compatibility format.

Public API::

    append_event(conn, event_type=..., team_id=..., run_id=..., ...) -> dict
    event_hash(prev_hash, event) -> str
    list_events(conn, team_id, ...) -> list[dict]
    verify_chain(conn, team_id) -> ChainCheck
    append_jsonl(team_dir, event) -> None
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .errors import UsageError
from .storage import now_iso

GENESIS = "0" * 64

EVENT_TYPES = frozenset({
    # v0.3 lifecycle
    "team_created", "task_created", "task_assigned", "task_claimed",
    "task_started", "task_submitted", "verification_started",
    "verification_passed", "verification_failed", "task_blocked",
    "task_released", "task_completed", "task_cancelled", "agent_started",
    "agent_finished", "agent_failed", "agent_timeout", "workspace_created",
    "workspace_integrated", "workspace_conflict", "token_usage_recorded",
    "team_completed", "run_started", "run_finished", "lead_token_rotated",
    "message_sent",
    # v2.1: the lead changed the agreed budget
    "budget_changed", "run_paused", "run_resumed",
    # v0.2 names kept so old logs stay valid
    "task_tokens", "verify_pass", "verify_fail", "round_limit",
})

_CHAIN_FIELDS = ("event_id", "run_id", "team_id", "task_id", "actor_id",
                "event_type", "timestamp", "metadata")


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    events_checked: int
    first_bad_seq: int | None


def _canonical(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_hash(prev_hash: str, event: dict) -> str:
    """sha256 over the previous hash and the canonical row JSON."""
    payload = {key: event.get(key) for key in _CHAIN_FIELDS}
    return hashlib.sha256(
        (prev_hash + "\n" + _canonical(payload)).encode("utf-8")).hexdigest()


def append_event(conn: sqlite3.Connection, *, event_type: str, team_id: str,
                 run_id: str, task_id: str | None = None, actor_id: str = "",
                 timestamp: str | None = None,
                 metadata: dict | None = None) -> dict:
    """Insert one event inside the caller's transaction; returns the row."""
    if event_type not in EVENT_TYPES:
        raise UsageError(
            f"unknown event type {event_type!r} "
            f"(known: {', '.join(sorted(EVENT_TYPES))})")
    import uuid

    ts = timestamp or now_iso()
    event = {
        "event_id": uuid.uuid4().hex,
        "run_id": run_id,
        "team_id": team_id,
        "task_id": task_id,
        "actor_id": actor_id,
        "event_type": event_type,
        "timestamp": ts,
        "metadata": metadata or {},
    }
    prev = conn.execute(
        "SELECT hash FROM events WHERE team_id = ? ORDER BY seq DESC LIMIT 1",
        (team_id,)).fetchone()
    prev_hash = prev[0] if prev else GENESIS
    row_hash = event_hash(prev_hash, event)
    conn.execute(
        "INSERT INTO events(event_id, run_id, team_id, task_id, actor_id,"
        " event_type, timestamp, metadata_json, prev_hash, hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event["event_id"], run_id, team_id, task_id, actor_id, event_type, ts,
         json.dumps(metadata or {}, ensure_ascii=False), prev_hash, row_hash))
    row = conn.execute("SELECT * FROM events WHERE event_id = ?",
                       (event["event_id"],)).fetchone()
    out = {key: row[key] for key in row.keys()}
    if isinstance(out.get("metadata_json"), str):
        try:
            out["metadata"] = json.loads(out["metadata_json"])
        except json.JSONDecodeError:
            out["metadata"] = {}
    out.pop("metadata_json", None)
    return out


def list_events(conn: sqlite3.Connection, team_id: str, *, run_id: str | None = None,
                task_id: str | None = None, event_type: str | None = None,
                since_seq: int | None = None, limit: int | None = None) -> list[dict]:
    sql, params = "SELECT * FROM events WHERE team_id = ?", [team_id]
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    if task_id is not None:
        sql += " AND task_id = ?"
        params.append(task_id)
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    if since_seq is not None:
        sql += " AND seq > ?"
        params.append(since_seq)
    sql += " ORDER BY seq"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    out = []
    for row in conn.execute(sql, params):
        item = {key: row[key] for key in row.keys()}
        if isinstance(item.get("metadata_json"), str):
            try:
                item["metadata"] = json.loads(item["metadata_json"])
            except json.JSONDecodeError:
                item["metadata"] = {}
        item.pop("metadata_json", None)
        out.append(item)
    return out


def verify_chain(conn: sqlite3.Connection, team_id: str) -> ChainCheck:
    prev = GENESIS
    checked = 0
    for row in conn.execute(
            "SELECT seq, event_id, run_id, team_id, task_id, actor_id, event_type,"
            " timestamp, metadata_json, prev_hash, hash FROM events"
            " WHERE team_id = ? ORDER BY seq",
            (team_id,)):
        event = {
            "event_id": row["event_id"], "run_id": row["run_id"],
            "team_id": row["team_id"], "task_id": row["task_id"],
            "actor_id": row["actor_id"], "event_type": row["event_type"],
            "timestamp": row["timestamp"],
            "metadata": _load_meta(row["metadata_json"]),
        }
        expected_prev = prev
        expected_hash = event_hash(expected_prev, event)
        if row["prev_hash"] != expected_prev or row["hash"] != expected_hash:
            return ChainCheck(ok=False, events_checked=checked,
                              first_bad_seq=row["seq"])
        prev = row["hash"]
        checked += 1
    return ChainCheck(ok=True, events_checked=checked, first_bad_seq=None)


def _load_meta(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def append_jsonl(team_dir, event: dict) -> None:
    """Mirror one event to events.jsonl in the v0.2-compatible shape."""
    from pathlib import Path

    record = {
        "ts": event.get("timestamp") or now_iso(),
        "type": event.get("event_type", ""),
        "event_id": event.get("event_id"),
        "run_id": event.get("run_id"),
        "team_id": event.get("team_id"),
        "task_id": event.get("task_id"),
        "actor": event.get("actor_id"),
        "seq": event.get("seq"),
        "hash": event.get("hash"),
    }
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            record.setdefault(key, value)
    path = Path(team_dir) / "events.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
