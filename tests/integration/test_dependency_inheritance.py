"""RED SPEC, Milestone 1 "Engine Integrity": dependency inheritance
(v2.1 spec 3.3, task dep-inherit).

Dependent tasks inherit the ACCEPTED results of their parents. Engine
2.0.0 only orders execution by deps: op_claim refuses until the deps are
completed, but the dependent worktree is grown from the run base or from
the task's own checkpoint (engine._prepare_workspace), so the accepted
results of the parents are not in it.

These tests describe REQUIRED behavior that is not implemented yet. They
are EXPECTED TO FAIL until dependency inheritance lands, each on an
assertion about the missing behavior, and they must pass unchanged once
it is implemented.

Public surface named by this spec:

- engine.op_inspect(root, team, task_id)["task"]["dependency_base"] is
  the base a worktree was built from: a list with one entry per parent
  task, {"task": <parent id>, "accepted_commit_sha": <sha>} where sha is
  the accepted commit of that parent. The order is free but stable
  across rebuilds; a task without deps has [].
- A conflict between the accepted results of the parents is reported by
  engine.op_claim raising StateRejected whose message names the
  conflict, the file and both parents.

Everything runs through the public operations (op_init, op_claim,
op_submit, op_verify, op_release, op_inspect, op_diff) on real git
repositories in temporary directories.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.errors import StateRejected
from agent_teams.storage import open_store

TEAM = "t"
_MISSING = object()


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


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
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def task(tid, assignee="w1", **kw):
    base = {"id": tid, "title": tid, "assignee": assignee,
            "files_read": [], "files_write": [f"out/{tid}.md"],
            "acceptance": ["done"], "deps": []}
    base.update(kw)
    return base


def init_team(project, tasks, **config):
    spec_file = project / "spec.json"
    spec_file.write_text(json.dumps({"config": config, "tasks": tasks}),
                         encoding="utf-8")
    created = engine.op_init(project, TEAM, spec_file)
    git(project, "add", "-A")
    git(project, "commit", "-qm", "spec")
    return created


def pass_ok(project, tid, lead):
    return engine.op_verify(project, TEAM, tid, "pass", "", lead, by="v",
                            override_reason="test acceptance")


def commit_file(workspace, path, text):
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", f"write {path}")


def accept(project, tid, lead, path=None, text=None):
    """claim -> write -> commit -> submit -> PASS; returns (claim, submit)."""
    claim = engine.op_claim(project, TEAM, tid, "w1")
    commit_file(Path(claim["workspace"]), path or f"out/{tid}.md",
                text if text is not None else f"result of {tid}\n")
    submitted = engine.op_submit(project, TEAM, tid, "w1",
                                 claim["execution_token"])
    assert pass_ok(project, tid, lead)["status"] == "completed"
    return claim, submitted


def task_row(project, tid):
    with open_store(project / ".agent-teams" / TEAM) as store:
        return store.get_task(TEAM, tid)


def accepted_changes(project, sha, run_base):
    """{path: content} of everything an accepted commit changed since the
    run base: the results of a task, including what it inherited."""
    paths = git(project, "diff", "--name-only", run_base, sha).split()
    return {path: git(project, "show", f"{sha}:{path}") for path in paths}


def files_in(workspace, paths):
    return {path: (workspace / path).read_text(encoding="utf-8")
            for path in paths}


def changed_paths(workspace, base):
    """Paths that differ between a commit and the HEAD of a worktree."""
    return git(workspace, "diff", "--name-only", base, "HEAD").split()


def dependency_base(project, tid):
    """The dependency base recorded for a task (the public surface)."""
    row = engine.op_inspect(project, TEAM, tid)["task"]
    base = row.get("dependency_base", _MISSING)
    assert base is not _MISSING, (
        f"task {tid}: engine.op_inspect() reports no 'dependency_base'; "
        "the engine does not record the base a worktree is built from")
    return base


def by_parent(entries):
    """{parent id: accepted sha} of a dependency base; checks its shape."""
    assert isinstance(entries, list), entries
    shas = {}
    for entry in entries:
        assert isinstance(entry, dict), entry
        assert set(entry) == {"task", "accepted_commit_sha"}, entry
        parent = entry["task"]
        assert isinstance(parent, str) and parent not in shas, entry
        assert isinstance(entry["accepted_commit_sha"], str), entry
        shas[parent] = entry["accepted_commit_sha"]
    return shas


def accepted_state(project, tid):
    """What the claim of another task must never touch: the submissions of
    a task, its accepted SHA, and the refs (branch, pinned snapshot) that
    hold its accepted commit."""
    row = task_row(project, tid)
    with open_store(project / ".agent-teams" / TEAM) as store:
        submissions = store.list_submissions(TEAM, tid)
    refs = [f"refs/heads/{row['branch']}"]
    refs += [f"refs/agent-teams/{TEAM}/{tid}/{s['id']}" for s in submissions]
    return {"status": row["status"],
            "accepted_commit_sha": row["accepted_commit_sha"],
            "head_commit": row["head_commit"],
            "submissions": submissions,
            "refs": {ref: git(project, "rev-parse", ref).strip()
                     for ref in refs}}


def test_child_sees_accepted_parent_results(git_project):
    """v2.1 spec 3.3, dep-inherit AC-001: "a child task (deps) sees in its
    worktree the results of its parent tasks after their PASS".

    The results of a parent are its accepted commit, including what the
    parent inherited itself (b depends on g). They are the base of the
    child's fresh worktree: committed and clean, exactly the run base plus
    the parents' changes, and not part of the child's own diff.
    """
    created = init_team(git_project, [
        task("g"), task("a"), task("b", deps=["g"]),
        task("c", deps=["a", "b"])])
    lead = created["lead_token"]
    for tid in ("g", "a", "b"):
        accept(git_project, tid, lead)
    expected = {"out/a.md": "result of a\n", "out/b.md": "result of b\n",
                "out/g.md": "result of g\n"}

    claim = engine.op_claim(git_project, TEAM, "c", "w1")
    ws = Path(claim["workspace"])
    for path in sorted(expected):
        assert (ws / path).is_file(), (
            f"the fresh worktree of dependent task c lacks {path}: the "
            "accepted results of its parents are not inherited")
    assert files_in(ws, expected) == expected
    assert git(ws, "status", "--porcelain") == ""
    assert changed_paths(ws, created["run_base"]) == sorted(expected)

    # the inherited files are the base of the child, not its own writes
    commit_file(ws, "out/c.md", "uses a and b\n")
    submitted = engine.op_submit(git_project, TEAM, "c", "w1",
                                 claim["execution_token"])
    assert submitted["write_set_violations"] == []
    own_writes = engine.op_diff(git_project, TEAM, "c")["changed_files"]
    assert own_writes == ["out/c.md"]
    assert pass_ok(git_project, "c", lead)["status"] == "completed"


def test_independent_tasks_stay_isolated(git_project):
    """v2.1 spec 3.3, dep-inherit AC-001: "independent tasks remain
    isolated": a task without deps does NOT see other tasks' files.

    Inheritance is keyed by deps only. The file checks below already hold
    in engine 2.0.0 and guard the implementation; what is missing is the
    explicit record: the dependency base of such a task is empty.
    """
    created = init_team(git_project, [task("a"), task("b"), task("x")])
    lead = created["lead_token"]
    foreign = {}
    for tid in ("a", "b"):
        submitted = accept(git_project, tid, lead)[1]
        foreign.update(accepted_changes(git_project, submitted["commit_sha"],
                                        created["run_base"]))
    assert sorted(foreign) == ["out/a.md", "out/b.md"]

    claim = engine.op_claim(git_project, TEAM, "x", "w1")
    ws = Path(claim["workspace"])
    for path in foreign:
        assert not (ws / path).exists(), (
            f"task x has no deps but its worktree holds {path}")
    assert changed_paths(ws, created["run_base"]) == []
    assert dependency_base(git_project, "x") == []


def test_dependency_base_is_recorded_and_deterministic(git_project):
    """v2.1 spec 3.3, dep-inherit AC-002: "the dependency base is
    deterministic (repeated builds give the same composition)".

    The claim records the base it built the worktree from: the parent ids
    with their ACCEPTED SHAs (a parent branch that moved after its PASS
    contributes nothing). Building it again from the same parents
    (release, then claim again) gives the same record and the same
    content.
    """
    created = init_team(git_project, [
        task("a"), task("b"), task("c", deps=["a", "b"])],
        max_verification_failures=1)
    lead = created["lead_token"]
    claims, shas = {}, {}
    for tid in ("a", "b"):
        claims[tid], submitted = accept(git_project, tid, lead)
        shas[tid] = submitted["commit_sha"]
    # branches are mutable: an unverified commit lands on the branch of b
    # after its PASS; only the accepted SHA of a parent may be inherited
    commit_file(Path(claims["b"]["workspace"]), "out/smuggled.md",
                "not verified\n")
    branch_b = f"refs/heads/agent-teams/{TEAM}/b/a1"
    assert git(git_project, "rev-parse", branch_b).strip() != shas["b"]
    expected = {}
    for sha in shas.values():
        expected.update(accepted_changes(git_project, sha,
                                         created["run_base"]))

    # build 1
    claim = engine.op_claim(git_project, TEAM, "c", "w1")
    ws = Path(claim["workspace"])
    base = dependency_base(git_project, "c")
    assert by_parent(base) == shas
    assert files_in(ws, expected) == expected
    assert changed_paths(ws, created["run_base"]) == sorted(expected)

    # the draft of the child fails verification: blocked, then released
    commit_file(ws, "out/c.md", "draft\n")
    engine.op_submit(git_project, TEAM, "c", "w1", claim["execution_token"])
    blocked = engine.op_verify(git_project, TEAM, "c", "fail", "not done",
                               lead)
    assert blocked["status"] == "blocked"
    released = engine.op_release(git_project, TEAM, "c", lead)
    assert released["status"] == "pending"

    # build 2: the same parents, the same dependency base and composition
    rebuilt = engine.op_claim(git_project, TEAM, "c", "w1")
    assert rebuilt["attempt"] == 2
    ws = Path(rebuilt["workspace"])
    assert dependency_base(git_project, "c") == base
    assert files_in(ws, expected) == expected
    assert not (ws / "out/smuggled.md").exists()


def test_parent_conflict_detected_before_executor_starts(git_project):
    """v2.1 spec 3.3, dep-inherit AC-002: "a dependency conflict is detected
    BEFORE the executor starts and does not lose results".

    Two accepted parents edited the same lines of one file (the lead
    allowed the write overlap). Claiming the child must be rejected with a
    StateRejected that explains the conflict; the parents stay exactly as
    accepted and the child stays pending.
    """
    shared = "out/shared.md"
    edits = {tid: f"{tid} line 1\nbase line 2\n" for tid in ("p1", "p2")}
    commit_file(git_project, shared, "base line 1\nbase line 2\n")
    created = init_team(git_project, [
        task("p1", files_write=[shared]), task("p2", files_write=[shared]),
        task("c", deps=["p1", "p2"])],
        allow_write_overlaps=True,
        allow_write_overlaps_reason="both parents edit one file on purpose")
    lead = created["lead_token"]
    accepted = {tid: accept(git_project, tid, lead, path=shared, text=text)
                for tid, text in edits.items()}
    before = {tid: accepted_state(git_project, tid) for tid in accepted}
    for tid, (_claim, submitted) in accepted.items():
        assert before[tid]["accepted_commit_sha"] == submitted["commit_sha"]
    project_head = git(git_project, "rev-parse", "HEAD").strip()
    child = task_row(git_project, "c")

    with pytest.raises(StateRejected, match="(?i)conflict") as exc:
        engine.op_claim(git_project, TEAM, "c", "w1")
    message = str(exc.value)
    assert shared in message, message
    assert "p1" in message and "p2" in message, message

    # nothing is lost: the parents are exactly as they were accepted
    assert git(git_project, "rev-parse", "HEAD").strip() == project_head
    for tid, (claim, submitted) in accepted.items():
        assert accepted_state(git_project, tid) == before[tid]
        ws = Path(claim["workspace"])
        head = git(ws, "rev-parse", "HEAD").strip()
        assert head == submitted["commit_sha"]
        assert (ws / shared).read_text(encoding="utf-8") == edits[tid]

    # the child stays pending: no owner, no token, no attempt consumed
    row = task_row(git_project, "c")
    assert row["status"] == "pending"
    assert row["owner"] is None and row["execution_token_hash"] is None
    assert row["attempt"] == child["attempt"]
