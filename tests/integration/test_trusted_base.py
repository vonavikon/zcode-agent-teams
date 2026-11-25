# RED spec for Milestone 1 "Engine Integrity" (team m1, task split-base).
#
# These tests describe the REQUIRED behavior of v2.1 spec 3.2 (trusted base
# vs draft checkpoint). The engine does not implement it yet, so this module
# is expected to FAIL until it does. A failure here is the specification
# doing its job: do not bend the tests to match the current engine.
"""Trusted base vs draft checkpoint: a failed attempt never becomes trusted.

v2.1 spec 3.2. The defect: ``op_claim`` takes the checkpoint for the next
execution attempt from the latest submission whose verification status is
``passed`` OR ``failed``. The failed version then becomes the base of the
new attempt's worktree and of its diff against ``files_write``, although
v2.0 SPEC.md (section 7) promises that "nothing from an old attempt is ever
trusted as the next attempt's starting point beyond the last verified
submission SHA".

Required behavior, in short:

* three version notions on a task: ``trusted_base_commit`` (the trusted
  base), ``draft_checkpoint_commit`` (the draft a failed attempt left, kept
  for continuation) and ``accepted_commit_sha`` (the accepted result);
* a failed attempt does not become trusted: the trusted base of the next
  attempt is unchanged, its draft stays available for continuation and is
  marked untrusted;
* cumulative changes are checked against the trusted base, so a violation
  found earlier cannot be hidden by resubmitting (while a retry that removes
  the offending file is clean);
* submissions record which base each attempt used.

Every test builds a real git repository in a temporary directory and talks
to the engine through its public ``engine.op_*`` functions only. The new
fields are read through ``engine.op_inspect(...)["task"]``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_teams import engine

TEAM = "t"
TASK = "a"
EXECUTOR = "w1"
VERIFIER = "verifier"
# files_write of the task is exactly one file; everything else is outside.
ALLOWED = "out/a.md"
OFFENDING = "smuggled.txt"
FEEDBACK = "write outside files_write"
ATTEMPT_1_TEXT = "attempt 1\n"
OVERRIDE = "the lead accepts the result"

# The first FAIL blocks the task, so "release" (lead) is the documented way
# back to pending and on to a new execution attempt. Three releases allowed.
CONFIG = {"max_verification_failures": 1, "max_execution_retries": 3}

# The three version notions of a task (spec 3.2), by their public names.
VERSION_NOTIONS = {
    "trusted_base_commit":
        "the trusted base: run base or the last ACCEPTED result",
    "draft_checkpoint_commit":
        "the draft a failed attempt left, kept for continuation",
    "accepted_commit_sha": "the accepted result",
}


# ---------------------------------------------------------------- git helpers


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} in {cwd}: {proc.stderr}")
    return proc.stdout


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(repo), capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def write(workspace: Path, relpath: str, text: str) -> None:
    path = workspace / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_all(workspace: Path, message: str) -> None:
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", message)


@pytest.fixture
def git_project(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@e.i")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base-0")
    (tmp_path / "second.txt").write_text("2\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base-1")
    return tmp_path


# ------------------------------------------------------- engine helpers


def task_spec() -> dict:
    return {"id": TASK, "title": TASK, "assignee": EXECUTOR,
            "files_read": [], "files_write": [ALLOWED],
            "acceptance": ["done"], "deps": []}


def init_team(project: Path) -> dict:
    spec_file = project / "spec.json"
    spec = {"config": CONFIG, "tasks": [task_spec()]}
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    created = engine.op_init(project, TEAM, spec_file)
    git(project, "add", "-A")
    git(project, "commit", "-qm", "spec")
    return created


def task_row(project: Path) -> dict:
    """The task exactly as the engine publishes it to inspectors."""
    return engine.op_inspect(project, TEAM, TASK)["task"]


def version_field(task: dict, name: str):
    """One version notion of a task, as engine.op_inspect publishes it.

    A missing field IS the missing behavior under specification, so it is
    reported as an assertion that names the intended public surface.
    """
    assert name in task, (
        f"engine.op_inspect(...)['task'] must expose {name!r} "
        f"(v2.1 spec 3.2: {VERSION_NOTIONS.get(name, 'version notion')})")
    return task[name]


def submission_history(project: Path) -> dict:
    """Submissions of the task keyed by execution attempt, as published by
    engine.op_inspect."""
    info = engine.op_inspect(project, TEAM, TASK)
    assert "submissions" in info, (
        "engine.op_inspect(...) must expose the submission history "
        "('submissions'), where each attempt records the base it used "
        f"(v2.1 spec 3.2); inspect keys present: {sorted(info)}")
    return {record["attempt"]: record for record in info["submissions"]}


def claim(project: Path) -> SimpleNamespace:
    result = engine.op_claim(project, TEAM, TASK, EXECUTOR)
    return SimpleNamespace(number=result["attempt"],
                           token=result["execution_token"],
                           workspace=Path(result["workspace"]))


def submit(project: Path, attempt: SimpleNamespace) -> dict:
    return engine.op_submit(project, TEAM, TASK, EXECUTOR, attempt.token)


def fail_verification(project: Path, lead: str) -> dict:
    return engine.op_verify(project, TEAM, TASK, "fail", FEEDBACK, lead,
                            by=VERIFIER)


def pass_with_override(project: Path, lead: str) -> dict:
    return engine.op_verify(project, TEAM, TASK, "pass", "", lead,
                            by=VERIFIER, override_reason=OVERRIDE)


def reclaim(project: Path, lead: str,
            after: SimpleNamespace) -> SimpleNamespace:
    """blocked -> pending (the lead releases the task) -> a new attempt."""
    engine.op_release(project, TEAM, TASK, lead,
                      reason="retry after a failed attempt")
    again = claim(project)
    assert again.number == after.number + 1, (
        "the retry must be a NEW execution attempt, not the same one")
    return again


def failed_attempt_then_reclaim(project: Path) -> SimpleNamespace:
    """Attempt 1 writes inside and outside files_write and fails; the lead
    requeues the task and attempt 2 is claimed.

    With CONFIG the failed round blocks the task; op_release returns it to
    pending and op_claim starts the next attempt. This is the public route
    on which a failed version could become the base of a new attempt.
    """
    created = init_team(project)
    scene = SimpleNamespace(lead=created["lead_token"],
                            run_base=created["run_base"])
    assert scene.run_base
    scene.first = claim(project)
    scene.row_at_first_claim = task_row(project)
    write(scene.first.workspace, ALLOWED, ATTEMPT_1_TEXT)
    write(scene.first.workspace, OFFENDING, "outside files_write\n")
    commit_all(scene.first.workspace, "attempt 1: work and a file outside "
                                      "files_write")
    scene.sub1 = submit(project, scene.first)
    scene.failed_sha = scene.sub1["commit_sha"]
    # the violation IS found in attempt 1
    assert scene.sub1["write_set_violations"] == [OFFENDING]
    assert fail_verification(project, scene.lead)["status"] == "blocked"
    scene.second = reclaim(project, scene.lead, scene.first)
    return scene


# ---------------------------------------------------------------- the spec


def test_failed_attempt_does_not_become_trusted_base(git_project):
    """Spec 3.2: "a FAILED attempt does not become trusted: after fail, the
    new attempt's trusted base is unchanged".

    trusted_base_commit stays the run base across a failed attempt. The
    failed version is neither the trusted base of the retry nor an accepted
    result.
    """
    scene = failed_attempt_then_reclaim(git_project)
    trusted_before = version_field(scene.row_at_first_claim,
                                   "trusted_base_commit")
    row_after = task_row(git_project)
    trusted_after = version_field(row_after, "trusted_base_commit")

    assert trusted_before == scene.run_base
    assert trusted_after == trusted_before
    assert trusted_after != scene.failed_sha, (
        "the failed version became the trusted base of the new attempt")
    assert version_field(row_after, "accepted_commit_sha") is None


def test_draft_is_preserved_for_continuation(git_project):
    """Spec 3.2: "the draft is preserved and reachable (a new claim may
    continue from the draft, but the draft is marked untrusted)".

    draft_checkpoint_commit names the commit the failed attempt left, the
    new attempt really continues from it, and nothing calls it trusted.
    """
    scene = failed_attempt_then_reclaim(git_project)
    row = task_row(git_project)
    draft = version_field(row, "draft_checkpoint_commit")

    # preserved: the draft is the failed attempt's version, and its content
    # is still retrievable from the repository and anchored by a ref
    assert draft == scene.failed_sha
    assert git(git_project, "cat-file", "-t", draft).strip() == "commit"
    assert git(git_project, "show", f"{draft}:{ALLOWED}") == ATTEMPT_1_TEXT
    assert git(git_project, "for-each-ref", f"--contains={draft}",
               "--format=%(refname)").strip()

    # reachable: the new claim continues from the draft, not from scratch
    head = git(scene.second.workspace, "rev-parse", "HEAD").strip()
    assert is_ancestor(scene.second.workspace, draft, head), (
        "the new attempt does not continue from the draft")
    assert (scene.second.workspace / ALLOWED).read_text(
        encoding="utf-8") == ATTEMPT_1_TEXT

    # ... but it is marked untrusted
    trusted = version_field(row, "trusted_base_commit")
    assert trusted == scene.run_base
    assert trusted != draft, "the draft is recorded as the trusted base"
    assert version_field(row, "accepted_commit_sha") is None


def test_violation_cannot_be_hidden_by_retry(git_project):
    """Spec 3.2: "cumulative changes are checked against the TRUSTED base: a
    file outside files_write introduced in attempt 1 and still present in
    attempt 2 is still reported as a violation in attempt 2".

    Attempt 2 goes on with allowed work and simply leaves the offending
    file in place. The violation must still be reported, and the gate must
    still refuse to accept the result.
    """
    scene = failed_attempt_then_reclaim(git_project)

    write(scene.second.workspace, ALLOWED, "attempt 2\n")
    commit_all(scene.second.workspace, "attempt 2: allowed work only")
    sub2 = submit(git_project, scene.second)
    assert sub2["commit_sha"] != scene.failed_sha

    assert sub2["write_set_violations"] == [OFFENDING], (
        "the violation found in attempt 1 disappeared from attempt 2")
    diff = engine.op_diff(git_project, TEAM, TASK)
    assert diff["write_set_violations"] == [OFFENDING]
    assert sorted(diff["changed_files"]) == sorted([ALLOWED, OFFENDING]), (
        "the recorded change set must be cumulative: trusted base -> HEAD")

    outcome = pass_with_override(git_project, scene.lead)
    assert outcome["status"] != "completed", (
        "a PASS accepted a result that still contains the offending file")
    assert task_row(git_project)["accepted_commit_sha"] is None


def test_retry_that_removes_the_offending_file_is_clean(git_project):
    """Spec 3.2: "cumulative changes are checked against the TRUSTED base".

    The boundary of that rule: it is a diff against the trusted base, not a
    sticky mark. When the retry removes the offending file, the cumulative
    diff no longer contains it and the retry can be accepted.
    """
    scene = failed_attempt_then_reclaim(git_project)

    (scene.second.workspace / OFFENDING).unlink()
    write(scene.second.workspace, ALLOWED, "attempt 2\n")
    commit_all(scene.second.workspace, "attempt 2: the offending file is "
                                       "removed")
    sub2 = submit(git_project, scene.second)

    assert sub2["write_set_violations"] == [], (
        "removing the offending file is reported as a write-set violation")
    diff = engine.op_diff(git_project, TEAM, TASK)
    assert diff["write_set_violations"] == []
    assert diff["changed_files"] == [ALLOWED]

    assert pass_with_override(git_project, scene.lead)["status"] == "completed"
    assert task_row(git_project)["accepted_commit_sha"] == sub2["commit_sha"]


def test_versions_are_bound_to_sha(git_project):
    """Spec 3.2: "every version has history bound to a SHA:
    submissions/attempts record which base each attempt used", and "three
    version notions on a task: trusted_base_commit (...),
    draft_checkpoint_commit (...), accepted_commit_sha".

    Two attempts fail; the second one continues from the draft of the first.
    Afterwards the submission of every attempt names its own version and
    the base that attempt was checked against, and the task names each
    version by its role.
    """
    created = init_team(git_project)
    run_base, lead = created["run_base"], created["lead_token"]
    assert run_base
    shas = {}
    attempt = claim(git_project)
    for number in (1, 2):
        write(attempt.workspace, ALLOWED, f"attempt {number}\n")
        if number == 1:
            write(attempt.workspace, OFFENDING, "outside files_write\n")
        commit_all(attempt.workspace, f"attempt {number}")
        shas[number] = submit(git_project, attempt)["commit_sha"]
        assert fail_verification(git_project, lead)["status"] == "blocked"
        attempt = reclaim(git_project, lead, attempt)
    assert attempt.number == 3
    row_at_third_claim = task_row(git_project)

    # history: one submission record per execution attempt, each bound to
    # the version it submitted ...
    history = submission_history(git_project)
    assert sorted(history) == [1, 2]
    assert [history[n]["commit_sha"] for n in (1, 2)] == [shas[1], shas[2]]
    # ... and naming the base its attempt was checked against: the trusted
    # base, never a failed version
    assert [history[n]["base_commit_sha"] for n in (1, 2)] == [run_base] * 2
    for number in (1, 2):
        assert is_ancestor(git_project, history[number]["base_commit_sha"],
                           history[number]["commit_sha"])
    assert [history[n]["verification_status"] for n in (1, 2)] == [
        "failed", "failed"]

    # the three version notions on the task, each a SHA known by its role
    assert version_field(row_at_third_claim, "trusted_base_commit") == run_base
    assert version_field(row_at_third_claim, "draft_checkpoint_commit") \
        == shas[2], "the draft is the version the LAST failed attempt left"
    assert version_field(row_at_third_claim, "accepted_commit_sha") is None
