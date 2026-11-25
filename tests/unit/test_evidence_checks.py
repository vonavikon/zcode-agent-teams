"""Evidence checks of the verification report (v2.1 spec 3.4).

validate_report runs two separate steps: validate_report_structure (pure
data, nothing is read from disk) and validate_evidence_existence (every
citation is bound to the snapshot root). These tests pin the rejection
codes, the error contract (criterion_id, path and reason in the message
and in the detail) and the legacy call without a snapshot root.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_teams.errors import UsageError
from agent_teams.verification import (
    validate_evidence_existence,
    validate_report,
    validate_report_structure,
)

SHA = "0" * 40
TASK = {
    "team_id": "t", "id": "a",
    "acceptance": [
        {"id": "AC-1", "description": "file present", "type": "file_check"},
        {"id": "AC-2", "description": "content sound", "type": "code_review"},
        {"id": "AC-3", "description": "taste", "type": "manual"},
    ],
}
SUBMISSION = {"id": "sub-1", "commit_sha": SHA}
APP_LINES = 6  # src/app.py of the snapshot; its last line has no newline

# Every one of these names an existing file outside the snapshot (or no
# file at all): the rule is about the path, not about existence.
UNSAFE_PATHS = [
    "../outside/secret.txt",
    "src/../../outside/secret.txt",
    "src\\..\\..\\outside\\secret.txt",
    "~/secret.txt",
    "C:/secret.txt",
    "//server/share/secret.txt",
    ".",
]


@pytest.fixture
def snapshot(tmp_path):
    """A snapshot directory, with an existing file right outside of it."""
    root = tmp_path / "snap"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_bytes(
        b"def one():\n    return 1\n\n\ndef four():\n    return 4")
    (root / "README.md").write_bytes(b"demo\n")
    (root / "empty.txt").write_bytes(b"")
    (root / "blob.bin").write_bytes(b"\xff\x00\xfe\n\x00\n")
    (root / "src" / "inner.py").symlink_to(root / "src" / "app.py")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_bytes(b"outside\n")
    (root / "src" / "escape.py").symlink_to(
        tmp_path / "outside" / "secret.txt")
    return root


def item(path, start=None, end=None):
    entry = {"path": path, "claim": "what the lines prove"}
    if start is not None:
        entry["line_start"] = start
    if end is not None:
        entry["line_end"] = end
    return entry


def criterion(cid, evidence=None, status="pass"):
    entry = {"criterion_id": cid, "status": status, "reason": "checked"}
    if evidence is not None:
        entry["evidence"] = evidence
    return entry


def report_for(*criteria) -> dict:
    return {"schema_version": "1.0", "team_id": "t", "task_id": "a",
            "submission_id": "sub-1", "commit_sha": SHA,
            "verifier": {"id": "v-1", "mode": "subagent"},
            "criteria": list(criteria), "verdict": "pass"}


def valid_report() -> dict:
    return report_for(
        criterion("AC-1", [item("src/app.py", 1)]),
        criterion("AC-2", [item("src/app.py", 2, 4), item("README.md", 1, 1)]),
        criterion("AC-3"))


def accepted(report, snapshot_root=None) -> dict:
    return validate_report(report, task=TASK, submission=SUBMISSION,
                           snapshot_root=snapshot_root)


def rejected(report, snapshot_root=None) -> UsageError:
    """validate_report must reject ``report``; returns the error."""
    with pytest.raises(UsageError) as excinfo:
        accepted(report, snapshot_root)
    return excinfo.value


def assert_rejected(error, code, criterion_id, path, *reason_parts):
    """The message and the detail carry the same criterion, path and reason."""
    assert error.message.startswith(f"VERIFICATION_REPORT_INVALID ({code}): ")
    assert error.detail["code"] == code
    assert error.detail["criterion_id"] == criterion_id
    assert error.detail["path"] == path
    reason = error.detail["reason"]
    assert reason and reason in error.message
    for part in reason_parts:
        assert part in reason
    if criterion_id:
        assert criterion_id in error.message
    if path:
        assert repr(path) in error.message


def with_evidence(report, index, *items) -> dict:
    report["criteria"][index]["evidence"] = list(items)
    return report


class TestCriterionUniqueness:
    def test_duplicate_criterion_id_is_rejected(self):
        report = valid_report()
        again = criterion("AC-1", [item("src/app.py", 1)])
        report["criteria"].insert(2, again)
        error = rejected(report)
        assert_rejected(error, "CRITERION_DUPLICATE", "AC-1", "",
                        "appears more than once",
                        "criteria[0] and criteria[2]")

    def test_two_verdicts_for_one_criterion_are_not_merged(self):
        # the second entry fails; before the check it replaced the first one
        report = valid_report()
        report["criteria"].append(criterion("AC-2", status="fail"))
        error = rejected(report)
        assert_rejected(error, "CRITERION_DUPLICATE", "AC-2", "")

    def test_structure_runs_before_existence(self, snapshot):
        report = with_evidence(valid_report(), 0, item("src/nope.py", 1))
        report["criteria"].append(criterion("AC-1"))
        error = rejected(report, snapshot)
        assert_rejected(error, "CRITERION_DUPLICATE", "AC-1", "")


class TestUnsafePaths:
    @pytest.mark.parametrize("path", UNSAFE_PATHS)
    def test_unsafe_path_is_rejected_with_a_snapshot(self, snapshot, path):
        report = with_evidence(valid_report(), 0, item(path, 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_PATH_UNSAFE", "AC-1", path,
                        "unsafe path")

    @pytest.mark.parametrize("path", UNSAFE_PATHS)
    def test_unsafe_path_is_rejected_without_a_snapshot_too(self, path):
        # path safety is lexical: it does not need the files
        report = with_evidence(valid_report(), 0, item(path, 1))
        error = rejected(report)
        assert_rejected(error, "EVIDENCE_PATH_UNSAFE", "AC-1", path,
                        "unsafe path")

    def test_absolute_path_into_the_snapshot_is_rejected(self, snapshot):
        # the file exists; an absolute path is still not snapshot-relative
        path = str(snapshot / "src" / "app.py")
        report = with_evidence(valid_report(), 0, item(path, 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_PATH_UNSAFE", "AC-1", path,
                        "absolute path")

    def test_symlink_leaving_the_snapshot_is_rejected(self, snapshot):
        # src/escape.py -> outside/secret.txt: the file exists, only its
        # resolved location tells that it is not part of the snapshot
        report = with_evidence(valid_report(), 0, item("src/escape.py", 1))
        # the lexical step alone cannot see symlinks
        assert accepted(report) is report
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_PATH_UNSAFE", "AC-1", "src/escape.py",
                        "symlink")

    def test_symlink_that_stays_inside_the_snapshot_is_allowed(self, snapshot):
        report = with_evidence(valid_report(), 0,
                               item("src/inner.py", APP_LINES))
        assert accepted(report, snapshot) is report


class TestEvidenceExistence:
    def test_missing_file(self, snapshot):
        report = with_evidence(valid_report(), 0, item("src/nope.py", 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_FILE_MISSING", "AC-1", "src/nope.py",
                        "does not exist")

    def test_directory_is_not_evidence(self, snapshot):
        report = with_evidence(valid_report(), 0, item("src", 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_FILE_MISSING", "AC-1", "src",
                        "not a regular file")

    def test_missing_line(self, snapshot):
        report = with_evidence(valid_report(), 0,
                               item("src/app.py", APP_LINES + 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_LINE_MISSING", "AC-1", "src/app.py",
                        f"line {APP_LINES + 1} does not exist",
                        f"the file has {APP_LINES} lines")

    def test_missing_line_in_an_empty_file(self, snapshot):
        report = with_evidence(valid_report(), 0, item("empty.txt", 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_LINE_MISSING", "AC-1", "empty.txt",
                        "the file has 0 lines")

    def test_line_end_beyond_the_end_of_the_file(self, snapshot):
        report = with_evidence(valid_report(), 0,
                               item("src/app.py", APP_LINES, APP_LINES + 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_RANGE_INVALID", "AC-1", "src/app.py",
                        "beyond the end of the file")

    @pytest.mark.parametrize("with_snapshot", [True, False])
    def test_line_end_below_line_start(self, snapshot, with_snapshot):
        # a pure data rule: it is rejected in both modes
        report = with_evidence(valid_report(), 0, item("src/app.py", 4, 2))
        error = rejected(report, snapshot if with_snapshot else None)
        assert_rejected(error, "EVIDENCE_RANGE_INVALID", "AC-1", "src/app.py",
                        "line_end 2 is below line_start 4")

    def test_every_evidence_item_is_checked(self, snapshot):
        # the first item is fine, the second one is not
        report = with_evidence(valid_report(), 1,
                               item("src/app.py", 1), item("README.md", 2))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_LINE_MISSING", "AC-2", "README.md",
                        "the file has 1 line")

    def test_evidence_of_a_manual_criterion_is_bound_too(self, snapshot):
        # optional there, but a citation that exists nowhere proves nothing
        report = with_evidence(valid_report(), 2, item("src/nope.py", 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_FILE_MISSING", "AC-3", "src/nope.py",
                        "does not exist")


class TestEvidenceShape:
    def test_content_criterion_needs_line_start(self):
        report = with_evidence(valid_report(), 0, item("src/app.py"))
        error = rejected(report)
        assert_rejected(error, "evidence", "AC-1", "src/app.py", "line_start")

    @pytest.mark.parametrize("start", [0, -1, "5", 1.5, True])
    def test_line_start_must_be_a_positive_integer(self, start):
        report = with_evidence(valid_report(), 0, item("src/app.py", start))
        error = rejected(report)
        assert_rejected(error, "EVIDENCE_RANGE_INVALID", "AC-1", "src/app.py",
                        "line_start must be a positive integer")

    @pytest.mark.parametrize("end", [0, -3, "2", 2.5, False])
    def test_line_end_must_be_a_positive_integer(self, end):
        report = with_evidence(valid_report(), 0, item("src/app.py", 1, end))
        error = rejected(report)
        assert_rejected(error, "EVIDENCE_RANGE_INVALID", "AC-1", "src/app.py",
                        "line_end must be a positive integer")

    def test_line_end_without_line_start(self):
        report = with_evidence(valid_report(), 2, item("src/app.py", end=3))
        error = rejected(report)
        assert_rejected(error, "EVIDENCE_RANGE_INVALID", "AC-3", "src/app.py",
                        "line_end is given without line_start")

    def test_evidence_must_be_an_array(self):
        report = valid_report()
        report["criteria"][2]["evidence"] = item("src/app.py", 1)
        error = rejected(report)
        assert_rejected(error, "evidence", "AC-3", "", "must be an array")

    def test_manual_criterion_with_a_reason_alone_still_passes(self, snapshot):
        report = valid_report()
        assert report["criteria"][2].get("evidence") is None
        assert accepted(report, snapshot) is report


class TestValidEvidence:
    def test_valid_evidence_in_a_real_snapshot(self, snapshot):
        report = valid_report()
        assert accepted(report, snapshot) is report

    def test_snapshot_root_may_be_a_string(self, snapshot):
        report = valid_report()
        assert accepted(report, str(snapshot)) is report

    def test_report_as_parsed_from_json(self, snapshot):
        report = json.loads(json.dumps(valid_report()))
        assert accepted(report, snapshot) == report

    def test_range_on_the_last_line_without_newline(self, snapshot):
        report = with_evidence(valid_report(), 0,
                               item("src/app.py", APP_LINES, APP_LINES))
        assert accepted(report, snapshot) is report

    def test_optional_evidence_may_cite_a_file_without_a_line(self, snapshot):
        report = with_evidence(valid_report(), 2, item("README.md"))
        assert accepted(report, snapshot) is report

    def test_optional_evidence_without_a_line_must_still_exist(self, snapshot):
        report = with_evidence(valid_report(), 2, item("src/nope.py"))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_FILE_MISSING", "AC-3", "src/nope.py",
                        "does not exist")

    def test_lines_of_a_binary_file_count_without_decoding(self, snapshot):
        report = with_evidence(valid_report(), 0, item("blob.bin", 2))
        assert accepted(report, snapshot) is report
        with_evidence(report, 0, item("blob.bin", 3))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_LINE_MISSING", "AC-1", "blob.bin",
                        "the file has 2 lines")


class TestSnapshotBinding:
    def test_files_are_read_only_inside_the_snapshot(self, snapshot,
                                                     monkeypatch):
        opened = []
        real_open = Path.open

        def spy(self, *args, **kwargs):
            opened.append(self)
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", spy)
        report = with_evidence(valid_report(), 2, item("src/escape.py", 1))
        error = rejected(report, snapshot)
        assert_rejected(error, "EVIDENCE_PATH_UNSAFE", "AC-3", "src/escape.py",
                        "symlink")
        root = snapshot.resolve()
        # AC-1 and AC-2 were read; the escaping file of AC-3 was not
        assert opened == [root / "src" / "app.py", root / "src" / "app.py",
                          root / "README.md"]

    def test_existence_is_a_separate_step(self, snapshot):
        report = with_evidence(valid_report(), 0, item("src/nope.py", 1))
        # step 1 alone: citing a file that exists nowhere is fine
        assert validate_report_structure(
            report, task=TASK, submission=SUBMISSION) is report
        # step 2 without a snapshot root: skipped
        assert validate_evidence_existence(report) is report
        # step 2 alone with the snapshot root: rejected
        with pytest.raises(UsageError) as excinfo:
            validate_evidence_existence(report, snapshot_root=snapshot)
        assert_rejected(excinfo.value, "EVIDENCE_FILE_MISSING", "AC-1",
                        "src/nope.py", "does not exist")

    def test_existence_step_is_safe_on_a_malformed_item(self, snapshot):
        report = report_for(criterion("AC-9", [item("src/app.py", 4, 2)]))
        with pytest.raises(UsageError) as excinfo:
            validate_evidence_existence(report, snapshot_root=snapshot)
        assert_rejected(excinfo.value, "EVIDENCE_RANGE_INVALID", "AC-9",
                        "src/app.py", "below line_start")

    @pytest.mark.parametrize("kind", ["missing", "empty", "blank", "file"])
    def test_unusable_snapshot_root_is_reported_as_such(self, tmp_path, kind):
        # "" would mean the current directory: that is never a snapshot
        not_a_dir = tmp_path / "x.txt"
        not_a_dir.write_text("x", encoding="utf-8")
        roots = {"missing": tmp_path / "nope", "empty": "", "blank": "   ",
                 "file": not_a_dir}
        root = roots[kind]
        with pytest.raises(UsageError) as excinfo:
            accepted(valid_report(), root)
        error = excinfo.value
        # a caller problem, not a report defect: no criterion to blame
        assert error.detail["code"] == "SNAPSHOT_ROOT_INVALID"
        assert error.detail["criterion_id"] == ""
        assert error.detail["path"] == str(root)
        assert error.detail["reason"] in error.message
        assert str(root) in error.message


class TestLegacyCall:
    def test_without_snapshot_root_nothing_is_read_from_disk(self):
        # the call engine.py makes: evidence may cite files that do not exist
        report = with_evidence(valid_report(), 0, item("src/nope.py", 10 ** 6))
        assert accepted(report) is report

    def test_legacy_call_keeps_the_structural_rejections(self):
        # ... but it still rejects what is wrong with the report itself
        report = with_evidence(valid_report(), 0,
                               item("../outside/secret.txt", 1))
        assert_rejected(rejected(report), "EVIDENCE_PATH_UNSAFE", "AC-1",
                        "../outside/secret.txt")
        with_evidence(report, 0, item("src/app.py", 4, 2))
        assert_rejected(rejected(report), "EVIDENCE_RANGE_INVALID", "AC-1",
                        "src/app.py")
