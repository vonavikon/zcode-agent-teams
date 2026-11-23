"""Verification report validation (hardening spec 4).

A PASS verdict requires a structured report that matches the current
submission: schema shape, team, task, submission_id, commit SHA, full
criterion coverage, no failed required criterion, evidence with file:line
for content-analyzing criteria, and a verdict consistent with the
criteria. The engine checks structure and cross-references; it cannot
cryptographically prove the report was written by an independent agent,
and that limitation is documented in docs/security.md.

A lead override with an explicit reason bypasses the report requirement
and is audited (submission status 'overridden').
"""
from __future__ import annotations

from pathlib import Path

from .errors import UsageError

REPORT_SCHEMA_VERSION = "1.0"
EVIDENCE_REQUIRED_TYPES = frozenset({
    "test", "code_review", "file_check", "security_check", "documentation",
})


def _fail(code: str, message: str) -> None:
    raise UsageError(f"VERIFICATION_REPORT_INVALID ({code}): {message}",
                     detail={"code": code})


def validate_report(report: object, *, task: dict,
                    submission: dict) -> dict:
    """Validate a verifier report against the current submission.

    Raises UsageError with a stable code on any violation; returns the
    report unchanged when everything holds.
    """
    if not isinstance(report, dict):
        _fail("shape", "the report must be a JSON object")
    required = ["schema_version", "team_id", "task_id", "submission_id",
                "commit_sha", "verifier", "criteria", "verdict"]
    for key in required:
        if key not in report:
            _fail("missing_key", f"'{key}' is missing")
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        _fail("schema_version",
              f"expected {REPORT_SCHEMA_VERSION!r}, got "
              f"{report['schema_version']!r}")
    if report["team_id"] != task["team_id"]:
        _fail("team_id", f"report is for team {report['team_id']!r}, "
                         f"this is {task['team_id']!r}")
    if report["task_id"] != task["id"]:
        _fail("task_id", f"report is for task {report['task_id']!r}, "
                         f"this is {task['id']!r}")
    if report["submission_id"] != submission["id"]:
        _fail("submission_id",
              f"report is for submission {report['submission_id']!r}, the "
              f"current one is {submission['id']!r}")
    if report["commit_sha"] != submission["commit_sha"]:
        _fail("commit_sha", "the SHA in the report does not match the "
              "submission snapshot")
    verifier = report["verifier"]
    if not isinstance(verifier, dict) or not verifier.get("id"):
        _fail("verifier", "verifier.id is required")

    criteria = report["criteria"]
    if not isinstance(criteria, list) or not criteria:
        _fail("criteria", "criteria must be a non-empty array")
    by_id = {}
    for entry in criteria:
        if not isinstance(entry, dict) or not entry.get("criterion_id"):
            _fail("criteria", "every criterion needs a criterion_id")
        if entry.get("status") not in ("pass", "fail"):
            _fail("criteria", f"criterion {entry.get('criterion_id')!r} "
                              "status must be pass or fail")
        by_id[entry["criterion_id"]] = entry

    acceptance = {c.get("id"): c for c in task.get("acceptance", [])
                  if isinstance(c, dict)}
    missing = sorted(set(acceptance) - set(by_id))
    if missing:
        _fail("coverage", "no verdict for criterion(s): " + ", ".join(missing))
    extra = sorted(set(by_id) - set(acceptance))
    if extra:
        _fail("coverage", "unknown criterion(s): " + ", ".join(extra))

    for cid, entry in by_id.items():
        spec = acceptance[cid]
        evidence = entry.get("evidence", [])
        needs_evidence = spec.get("type") in EVIDENCE_REQUIRED_TYPES
        if entry["status"] == "pass" and needs_evidence:
            if not evidence:
                _fail("evidence",
                      f"criterion {cid} passed without evidence; content "
                      "criteria need file:line evidence")
            for item in evidence:
                if not isinstance(item, dict) or not item.get("path"):
                    _fail("evidence", f"criterion {cid}: every evidence item "
                                      "needs a path")
                if not item.get("line_start"):
                    _fail("evidence",
                          f"criterion {cid}: evidence for {item['path']} "
                          "needs line_start")
        if entry["status"] == "fail" and not (entry.get("reason") or "").strip():
            _fail("reason", f"criterion {cid} failed without a reason")

    if report["verdict"] not in ("pass", "fail"):
        _fail("verdict", "verdict must be pass or fail")
    if report["verdict"] == "pass":
        failed_required = [cid for cid, entry in by_id.items()
                           if entry["status"] == "fail"
                           and acceptance[cid].get("required", True)]
        if failed_required:
            _fail("verdict", "verdict pass with failed required "
                  "criterion(s): " + ", ".join(sorted(failed_required)))
    return report


def report_from_path(path: Path) -> dict:
    import json

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("unreadable", f"cannot read the report: {exc}")
