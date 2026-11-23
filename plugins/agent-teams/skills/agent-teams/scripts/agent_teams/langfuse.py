"""Langfuse export from the SQLite state (v0.3 port of the v0.2 uploader).

Traces go to the OTLP endpoint, scores to POST /api/public/scores (the
events_only mode of self-hosted Langfuse v4 is enough). The export is
idempotent per run: a payload hash is recorded in the exports table, and an
identical re-export is skipped instead of duplicated.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import events as eventlog
from .errors import ExternalError
from .storage import Store, now_iso, open_store

RETRIES = 3
BACKOFF_S = 1.5


def _hex32(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _nano(iso: str) -> str:
    from datetime import datetime
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return str(int(dt.timestamp() * 1_000_000_000))


def _attr(key: str, value) -> dict:
    return {"key": key, "value": {"stringValue": value if isinstance(value, str)
                                  else json.dumps(value, ensure_ascii=False)}}


def _post(url: str, payload: dict, pub: str, sec: str) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(
                     f"{pub}:{sec}".encode()).decode()},
        method="POST")
    last_error = ""
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                resp.read()
                return
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            last_error = str(exc)[:300]
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * (attempt + 1))
    raise ExternalError(f"Langfuse upload failed after {RETRIES} attempts: "
                        f"{last_error}")


def _build_payload(store: Store, team: str, run: dict, name: str,
                   project_root: Path) -> tuple[str, list[dict], list[dict]]:
    trace_id = _hex32(run["id"])
    run_events = eventlog.list_events(store.conn, team, run_id=run["id"])
    span_root = _hex32(f"{run['id']}|root")[:16]
    started = run_events[0]["timestamp"] if run_events else run["started_at"]
    ended = run_events[-1]["timestamp"] if run_events else None
    usage = store.list_usage(team, run_id=run["id"])
    spans = [{
        "traceId": trace_id, "spanId": span_root,
        "name": name or f"agent-teams:{team}#{run['number']}",
        "startTimeUnixNano": _nano(run["started_at"]),
        "endTimeUnixNano": _nano(ended or started),
        "attributes": [
            _attr("langfuse.trace.name", name or f"agent-teams:{team}#{run['number']}"),
            _attr("langfuse.internal.is_app_root", "true"),
            _attr("langfuse.observation.type", "span"),
            _attr("langfuse.trace.metadata", {
                "team": team, "run": run["number"], "project": project_root.name,
                "tokens_total": sum(u.get("total_tokens") or 0 for u in usage),
                "models": sorted({u.get("model") for u in usage
                                  if u.get("model")}),
                "cost_usd": round(sum(
                    (u.get("cost_usd") or 0) for u in usage), 6)}),
        ],
    }]
    scores = []
    for task in store.list_tasks(team):
        attempts = store.list_attempts(team, task["id"])
        task_span = _hex32(f"{run['id']}|{task['id']}")[:16]
        starts = [task["claimed_at"] or task["created_at"]]
        ends = [task["completed_at"] or task["last_event_ts"] or ended or started]
        spans.append({
            "traceId": trace_id, "spanId": task_span, "parentSpanId": span_root,
            "name": f"{task['id']}: {task['title']}",
            "startTimeUnixNano": _nano(starts[0]),
            "endTimeUnixNano": _nano(ends[0]),
            "attributes": [
                _attr("langfuse.observation.type", "span"),
                _attr("langfuse.observation.metadata", {
                    "assignee": task["assignee"], "owner": task["owner"],
                    "lens": task["lens"], "status": task["status"],
                    "files_write": task["files_write"],
                    "verification_failures": task["verification_failures"],
                    "tokens": task["tokens"]}),
            ],
        })
        for attempt in attempts:
            spans.append({
                "traceId": trace_id,
                "spanId": _hex32(f"{run['id']}|{task['id']}|{attempt['attempt']}")[:16],
                "parentSpanId": task_span,
                "name": f"verification #{attempt['attempt']}: {attempt['verdict']}",
                "startTimeUnixNano": _nano(attempt["created_at"]),
                "endTimeUnixNano": _nano(attempt["created_at"]),
                "attributes": [
                    _attr("langfuse.observation.type", "span"),
                    _attr("langfuse.observation.metadata", {
                        "verifier": attempt["verifier"],
                        "feedback": attempt["feedback"][:500]}),
                ],
            })
            scores.append({
                "traceId": trace_id,
                "name": "verify-gate",
                "value": 1.0 if attempt["verdict"] == "pass" else 0.0,
                "dataType": "NUMERIC",
                "comment": f"task={task['id']} attempt={attempt['attempt']}",
            })
    return trace_id, spans, scores


def op_langfuse(root: Path, team: str, name: str = "") -> dict:
    from .engine import _open_team  # local import avoids a cycle at load time

    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3100").strip().rstrip("/")
    if not pub or not sec:
        raise ExternalError(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set in the "
            "environment")

    store, team_dir, team_row = _open_team(root, team)
    try:
        from .engine import _current_run

        with store.transaction():
            run = _current_run(store, team) or {}
        if not run:
            raise ExternalError(f"team {team} has no run to export")
        trace_id, spans, scores = _build_payload(store, team, run, name,
                                                 Path(team_row["project_root"]))
        payload = {"spans": spans, "scores": scores}
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()

        previous = store.get_export(team, run["id"], "langfuse")
        if previous and previous["status"] == "exported" \
                and previous["payload_hash"] == payload_hash:
            return {"skipped": "already exported", "trace_id": trace_id,
                    "spans": len(spans), "scores": len(scores)}

        _post(f"{host}/api/public/otel/v1/traces",
              {"resourceSpans": [{"scopeSpans": [
                  {"scope": {"name": "agent-teams"}, "spans": spans}]}]},
              pub, sec)
        for score in scores:
            _post(f"{host}/api/public/scores", score, pub, sec)
        with store.transaction():
            store.upsert_export({"team_id": team, "run_id": run["id"],
                                 "target": "langfuse", "trace_id": trace_id,
                                 "status": "exported",
                                 "attempts": (previous["attempts"] + 1) if previous
                                 else 1,
                                 "payload_hash": payload_hash,
                                 "exported_at": now_iso()})
        return {"trace_id": trace_id, "spans": len(spans), "scores": len(scores),
                "url": f"{host}/trace/{trace_id}"}
    finally:
        store.close()
