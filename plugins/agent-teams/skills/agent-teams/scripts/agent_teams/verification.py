"""Verification report validation (hardening spec 4, v2.1 spec 3.4).

A PASS verdict requires a structured report that matches the current
submission: schema shape, team, task, submission_id, commit SHA, full
criterion coverage with every criterion listed exactly once, no failed
required criterion, evidence with file:line for content-analyzing
criteria, and a verdict consistent with the criteria.

Validation runs in two separate steps:

1. ``validate_report_structure``: pure data validation, the filesystem
   is not read. Shape, cross-references, unique criterion ids, coverage,
   the shape of every evidence item and lexically safe evidence paths
   (project-relative, no '..').
2. ``validate_evidence_existence``: needs the snapshot root. Every
   evidence path must resolve inside that root (symlinks included) and
   the cited file and lines must exist there. Without a snapshot root
   the step is skipped, which is the legacy behaviour.

``validate_report`` runs both steps and is the entry point; its optional
``snapshot_root`` keyword switches step 2 on.

Neither step judges whether the cited lines prove the claim. Semantic
validity is not attempted in code: it stays with the LLM (or human)
verifier that wrote the ``claim`` field.

Every rejection is a UsageError whose message and ``detail`` carry the
same four fields: ``code`` (stable), ``criterion_id`` ("" when the report
as a whole is rejected), ``path`` (the evidence path as cited, "" when
there is none) and ``reason``. Codes of the checks added with the
evidence binding:

- ``CRITERION_DUPLICATE``: a criterion_id is listed more than once
- ``EVIDENCE_PATH_UNSAFE``: absolute path, '..' or a symlink that
  leaves the snapshot root
- ``EVIDENCE_FILE_MISSING``: the cited file is not a regular file of the
  snapshot
- ``EVIDENCE_LINE_MISSING``: line_start is beyond the end of the file
- ``EVIDENCE_RANGE_INVALID``: a line number that is not a positive
  integer, line_end below line_start or beyond the end of the file

The lowercase codes (``shape``, ``coverage``, ``evidence``, ...) are the
checks that existed before this split and keep their meaning.

The engine checks structure, cross-references and existence; it cannot
cryptographically prove the report was written by an independent agent,
and that limitation is documented in docs/security.md.

A lead override with an explicit reason bypasses the report requirement
and is audited (submission status 'overridden').
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .errors import UsageError
from .validation import normalize_path

REPORT_SCHEMA_VERSION = "1.0"
EVIDENCE_REQUIRED_TYPES = frozenset({
    "test", "code_review", "file_check", "security_check", "documentation",
})

# Stable codes of the evidence checks (module docstring lists them all).
CODE_CRITERION_DUPLICATE = "CRITERION_DUPLICATE"
CODE_PATH_UNSAFE = "EVIDENCE_PATH_UNSAFE"
CODE_FILE_MISSING = "EVIDENCE_FILE_MISSING"
CODE_LINE_MISSING = "EVIDENCE_LINE_MISSING"
CODE_RANGE_INVALID = "EVIDENCE_RANGE_INVALID"
CODE_SNAPSHOT_ROOT = "SNAPSHOT_ROOT_INVALID"

# normalize_path looks at its root only when check_fs is true, so the
# structural step (which never reads the filesystem) passes a placeholder.
_LEXICAL_ONLY_ROOT = Path(".")
_READ_CHUNK = 1 << 16


def _fail(code: str, message: str, *, criterion_id: str = "", path: str = "",
          reason: str = "") -> None:
    """Raise one report rejection.

    ``message`` is what the CLI prints; ``detail`` repeats its parts as
    data so that a caller never has to parse the text.
    """
    raise UsageError(f"VERIFICATION_REPORT_INVALID ({code}): {message}",
                     detail={"code": code, "criterion_id": criterion_id,
                             "path": path, "reason": reason or message})


def _text(value: object) -> str:
    return value if isinstance(value, str) else repr(value)


def _reject_evidence(code: str, cid: object, raw: object, reason: str) -> None:
    """Reject one evidence item: the message names the criterion, the
    path as cited and the reason; the detail carries them as fields."""
    _fail(code, f"criterion {cid}: evidence {_text(raw)!r}: {reason}",
          criterion_id=_text(cid), path=_text(raw), reason=reason)


def _is_line_number(value: object) -> bool:
    # bool is an int subclass: True must not pass for line 1
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 1)


def _lines(count: int) -> str:
    return f"{count} line" + ("" if count == 1 else "s")


def _safe_rel(cid: object, raw: object, snapshot_root: Path | None) -> str:
    """Normalized POSIX form of an evidence path, or EVIDENCE_PATH_UNSAFE.

    The lexical rules always apply (same as files_read and files_write,
    through normalize_path): a non-empty project-relative path without
    '..'. With a snapshot root the path must also resolve inside it,
    symlinks included.
    """
    lexical_only = snapshot_root is None
    try:
        return normalize_path(
            _LEXICAL_ONLY_ROOT if lexical_only else snapshot_root, raw,
            check_fs=not lexical_only)
    except UsageError as exc:
        _reject_evidence(CODE_PATH_UNSAFE, cid, raw, f"unsafe path: {exc}")


def _check_evidence_item(cid: object, item: object, *, line_required: bool,
                         snapshot_root: Path | None = None
                         ) -> tuple[str, int | None, int | None]:
    """Shape and path safety of one evidence item.

    Returns the normalized path and the optional line range. Evidence of
    a passed content criterion needs line_start (``line_required``). Any
    other evidence item is optional, but when it is present it has to be
    well-formed: a citation that cannot be located proves nothing.
    """
    if not isinstance(item, dict) or not item.get("path"):
        _fail("evidence", f"criterion {cid}: every evidence item needs a path",
              criterion_id=_text(cid))
    raw = item["path"]
    rel = _safe_rel(cid, raw, snapshot_root)
    start, end = item.get("line_start"), item.get("line_end")
    if start is None and line_required:
        _fail("evidence",
              f"criterion {cid}: evidence {raw!r}: needs line_start",
              criterion_id=_text(cid), path=_text(raw))
    for name, value in (("line_start", start), ("line_end", end)):
        if value is not None and not _is_line_number(value):
            _reject_evidence(CODE_RANGE_INVALID, cid, raw,
                             f"{name} must be a positive integer, "
                             f"got {value!r}")
    if end is not None and start is None:
        _reject_evidence(CODE_RANGE_INVALID, cid, raw,
                         "line_end is given without line_start")
    if start is not None and end is not None and end < start:
        _reject_evidence(CODE_RANGE_INVALID, cid, raw,
                         f"line_end {end} is below line_start {start}")
    return rel, start, end


def _evidence_items(report: dict) -> Iterator[tuple[object, object]]:
    """Yield (criterion_id, item) for every evidence item of a report
    that passed validate_report_structure."""
    for entry in report["criteria"]:
        evidence = entry.get("evidence", [])
        if isinstance(evidence, list):
            for item in evidence:
                yield entry["criterion_id"], item


def _count_lines(path: Path, needed: int) -> int:
    """Number of lines in ``path``, read in binary to survive any encoding.

    Reading stops after ``needed`` lines, so the result is the exact line
    count only when it is below ``needed`` (the only case that is
    reported). A line ends with a newline; an unterminated last line
    counts too, which is how editors number lines.
    """
    lines, last = 0, b""
    with path.open("rb") as stream:
        while lines < needed:
            chunk = stream.read(_READ_CHUNK)
            if not chunk:
                if last and last != b"\n":
                    lines += 1
                break
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    return lines


def _snapshot_file_lines(cid: object, raw: object, target: Path,
                         needed: int) -> int:
    """Line count of one cited snapshot file, or the reason it is unusable.

    The file must exist in the snapshot and be a regular file that can be
    read; anything else is EVIDENCE_FILE_MISSING.
    """
    try:
        if not target.exists():
            _reject_evidence(CODE_FILE_MISSING, cid, raw,
                             "file does not exist in the snapshot")
        if not target.is_file():
            _reject_evidence(CODE_FILE_MISSING, cid, raw,
                             "not a regular file in the snapshot")
        return _count_lines(target, needed)
    except OSError as exc:
        _reject_evidence(CODE_FILE_MISSING, cid, raw,
                         f"cannot be read from the snapshot ({exc})")


def validate_report_structure(report: object, *, task: dict,
                              submission: dict) -> dict:
    """Step 1, structural validation: pure data, nothing is read from disk.

    Checks the report shape, its cross-references to the task and the
    submission, that every criterion appears exactly once and that all
    of them are covered, the evidence rules and the verdict consistency.

    Every evidence item present in the report must be well-formed: a
    non-empty path that is a lexically safe project-relative path (not
    absolute, no '..'), line numbers that are positive integers with
    line_end >= line_start, and line_start for the evidence of a passed
    content criterion. Evidence of other criteria stays optional.

    Raises UsageError on the first violation, returns the report as is
    otherwise. Whether a cited file exists is not a structural property:
    that is step 2, validate_evidence_existence.
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
    by_id: dict[object, dict] = {}
    first_seen: dict[object, int] = {}
    for index, entry in enumerate(criteria):
        if not isinstance(entry, dict) or not entry.get("criterion_id"):
            _fail("criteria", "every criterion needs a criterion_id")
        cid = entry["criterion_id"]
        if entry.get("status") not in ("pass", "fail"):
            _fail("criteria", f"criterion {cid!r} status must be pass or fail")
        if cid in by_id:
            # Two verdicts for one criterion: neither can be trusted
            _fail(CODE_CRITERION_DUPLICATE,
                  f"criterion_id {cid!r} appears more than once in criteria "
                  f"(criteria[{first_seen[cid]}] and criteria[{index}])",
                  criterion_id=_text(cid))
        first_seen[cid], by_id[cid] = index, entry

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
        line_required = (entry["status"] == "pass"
                         and spec.get("type") in EVIDENCE_REQUIRED_TYPES)
        if line_required and not evidence:
            _fail("evidence",
                  f"criterion {cid} passed without evidence; content "
                  "criteria need file:line evidence", criterion_id=_text(cid))
        if not isinstance(evidence, list):
            _fail("evidence", f"criterion {cid}: evidence must be an array",
                  criterion_id=_text(cid))
        for item in evidence:
            _check_evidence_item(cid, item, line_required=line_required)
        reason = (entry.get("reason") or "").strip()
        if entry["status"] == "fail" and not reason:
            _fail("reason", f"criterion {cid} failed without a reason",
                  criterion_id=_text(cid))

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


def validate_evidence_existence(report: dict, *,
                                snapshot_root: Path | None = None) -> dict:
    """Step 2, evidence existence: bind every citation to the snapshot.

    ``snapshot_root`` is the directory of the immutable snapshot the
    submission was pinned to. For every evidence item of a report that
    passed validate_report_structure: the path must resolve inside that
    root (a symlink may not leave it), the file must be a regular file
    there, line_start must exist and, when line_end is given, line_end
    must exist as well. Files are read from the snapshot root only.

    Without a snapshot root nothing is read and the report is returned
    as is: that is the legacy call, where only the structural rules
    (including lexical path safety) apply.

    Existence is all this step proves. Whether the cited lines support
    the claim is a semantic question that stays with the verifier; it is
    not attempted here.

    Raises UsageError on the first violation, returns the report as is
    otherwise.
    """
    if snapshot_root is None:
        return report
    named = str(snapshot_root).strip()
    try:
        # Path("") is the current directory: an empty root is no snapshot
        root = Path(snapshot_root).resolve() if named else None
        problem = None
        if root is None:
            problem = "it is empty"
        elif not root.is_dir():
            problem = "it is not a directory"
    except OSError as exc:
        problem = f"it cannot be resolved ({exc})"
    if problem:
        reason = f"the snapshot root is unusable: {problem}"
        raise UsageError(
            f"{reason}: {snapshot_root}",
            detail={"code": CODE_SNAPSHOT_ROOT, "criterion_id": "",
                    "path": str(snapshot_root), "reason": reason})

    for cid, item in _evidence_items(report):
        rel, start, end = _check_evidence_item(
            cid, item, line_required=False, snapshot_root=root)
        raw = item["path"]
        target = root.joinpath(*rel.split("/"))
        lines = _snapshot_file_lines(cid, raw, target,
                                     max(start or 1, end or 1))
        if start is not None and lines < start:
            _reject_evidence(CODE_LINE_MISSING, cid, raw,
                             f"line {start} does not exist "
                             f"(the file has {_lines(lines)})")
        if end is not None and lines < end:
            _reject_evidence(CODE_RANGE_INVALID, cid, raw,
                             f"line_end {end} is beyond the end of the file "
                             f"(the file has {_lines(lines)})")
    return report


def validate_report(report: object, *, task: dict, submission: dict,
                    snapshot_root: Path | None = None) -> dict:
    """Validate a verifier report against the current submission.

    Two steps, in this order: validate_report_structure (shape,
    cross-references, unique criteria, coverage, evidence shape and safe
    paths; nothing is read from disk), then validate_evidence_existence
    (the cited files and lines exist in the snapshot).

    ``snapshot_root`` is the directory of the immutable snapshot the
    submission was pinned to. When it is given, every evidence item has
    to resolve inside that root and exist there. When it is omitted (the
    legacy call) files are not touched and only the structural rules
    apply.

    Semantic validity, whether the cited lines really prove the claim, is
    not checked in code: that judgement stays with the LLM verifier.

    Raises UsageError with a stable code on the first violation; returns
    the report unchanged when everything holds.
    """
    validate_report_structure(report, task=task, submission=submission)
    validate_evidence_existence(report, snapshot_root=snapshot_root)
    return report


def report_from_path(path: Path) -> dict:
    import json

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("unreadable", f"cannot read the report: {exc}")
