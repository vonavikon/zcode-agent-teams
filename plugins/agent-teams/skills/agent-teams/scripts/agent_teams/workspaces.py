"""Per-task workspace isolation through git worktrees (spec section 5).

Each implementation task gets its own worktree and branch under
``.agent-teams/<team>/tasks/<task>/workspace``; the base commit is recorded
on the task row. A worktree isolates work between tasks; it does NOT by
itself restrict the executor to files_write, so the actual diff is captured
on submit and compared against the declared write set (engine.py).

Projects without git (or teams configured as ``shared``) fall back to
working directly in the project root: the lead is warned at init, and no
isolation is claimed.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ExternalError, UsageError


@dataclass(frozen=True)
class WorkspaceInfo:
    path: str
    branch: str
    base_commit: str


@dataclass(frozen=True)
class WorkspaceDiff:
    changed: tuple[str, ...]
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    renamed: tuple[str, ...]
    has_uncommitted: bool
    head_commit: str | None
    raw_status: str


def _git(args: list[str], cwd: Path, *,
         check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=120)
    if check and proc.returncode != 0:
        raise ExternalError(
            f"git {' '.join(args[:2])} failed in {cwd}: "
            f"{proc.stderr.strip()[:300]}")
    return proc


def is_git_project(root: Path) -> bool:
    try:
        proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                              cwd=str(root), capture_output=True, text=True,
                              timeout=30)
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def workspace_dir(team_root: Path, task_id: str, attempt: int = 1) -> Path:
    """Per-attempt workspace (hardening spec 3.3): every execution attempt
    gets its own directory, so a fenced-out executor cannot touch the new
    attempt's files."""
    if not task_id or "/" in task_id or ".." in task_id:
        raise UsageError(f"invalid task id for a workspace path: {task_id!r}")
    attempt = max(1, int(attempt))
    return team_root / "tasks" / task_id / "attempts" / str(attempt) / "workspace"


def attempt_dir(team_root: Path, task_id: str, attempt: int) -> Path:
    return workspace_dir(team_root, task_id, attempt).parent


def quarantine_dir(team_root: Path, task_id: str, attempt: int) -> Path:
    stamp = now_iso_filesafe()
    return team_root / "tasks" / task_id / "quarantine" / f"{attempt}-{stamp}"


def now_iso_filesafe() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def quarantine_attempt(team_root: Path, task_id: str,
                        attempt: int) -> Path | None:
    """Move a fenced attempt aside; its files stay inspectable for manual
    salvage but are never reused as an active workspace."""
    import shutil

    source = attempt_dir(team_root, task_id, attempt)
    if not source.exists():
        return None
    target = quarantine_dir(team_root, task_id, attempt)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return target


def branch_name(team: str, task_id: str, attempt: int = 1) -> str:
    """Per-attempt branch: attempts never share mutable refs either."""
    return f"agent-teams/{team}/{task_id}/a{max(1, int(attempt))}"


def create_workspace(project_root: Path, team_root: Path, team: str,
                     task_id: str, base_commit: str | None = None,
                     attempt: int = 1) -> WorkspaceInfo:
    """Create the per-attempt worktree of a task; returns its info."""
    if not is_git_project(project_root):
        raise ExternalError(
            f"no git repository at {project_root}: configure workspace "
            "'shared' or run outside git")
    target = workspace_dir(team_root, task_id, attempt)
    branch = branch_name(team, task_id, attempt)
    if base_commit is None:
        base_commit = _git(["rev-parse", "HEAD"], project_root).stdout.strip()
    if target.exists() and (target / ".git").exists():
        # An existing worktree is reused (recovery after a crash).
        return WorkspaceInfo(str(target), branch, base_commit)
    target.parent.mkdir(parents=True, exist_ok=True)
    if _branch_exists(project_root, branch):
        _git(["worktree", "add", str(target), branch], project_root)
    else:
        _git(["worktree", "add", "-b", branch, str(target), base_commit],
             project_root)
    return WorkspaceInfo(str(target), branch, base_commit)


def _branch_exists(project_root: Path, branch: str) -> bool:
    proc = subprocess.run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
                          cwd=str(project_root), capture_output=True, text=True,
                          timeout=30)
    return proc.returncode == 0


def capture_diff(workspace: Path, base_commit: str) -> WorkspaceDiff:
    """Changed files of a worktree versus its base commit.

    Covers committed changes (base..HEAD), staged and unstaged work, and
    untracked files; paths are POSIX-relative to the workspace root.
    """
    added, modified, deleted, renamed = [], [], [], []
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(workspace),
                          capture_output=True, text=True, timeout=30)
    head_commit = head.stdout.strip() if head.returncode == 0 else None

    name_status = subprocess.run(
        ["git", "diff", "--name-status", "-z", base_commit],
        cwd=str(workspace), capture_output=True, text=True, timeout=120)
    entries = [chunk for chunk in name_status.stdout.split("\0") if chunk]
    i = 0
    while i < len(entries):
        code = entries[i]
        if code.startswith("R"):
            renamed.append(entries[i + 2].replace("\\", "/"))
            i += 3
        else:
            path = entries[i + 1].replace("\\", "/")
            if code == "A":
                added.append(path)
            elif code == "D":
                deleted.append(path)
            else:
                modified.append(path)
            i += 2

    status = _git(["status", "--porcelain", "-z", "--untracked-files=all"],
                  workspace)
    records = [chunk for chunk in status.stdout.split("\0") if chunk]
    has_uncommitted = bool(records)
    index = 0
    while index < len(records):
        record = records[index]
        code, _, path = record[:2], record[2], record[3:]
        path = path.replace("\\", "/")
        index += 1
        if code.startswith("R") or code.startswith("C"):
            index += 1  # the copy/rename source follows as its own field
        if code == "??":
            added.append(path)
        elif code == "AD":
            deleted.append(path)  # added in the index, gone from disk
        elif "D" in code:
            deleted.append(path)
        elif "A" in code:
            added.append(path)
        elif path not in modified and path not in added and path not in deleted:
            modified.append(path)

    changed = sorted(set(added) | set(modified) | set(deleted) | set(renamed))
    return WorkspaceDiff(
        changed=tuple(changed), added=tuple(sorted(set(added))),
        modified=tuple(sorted(set(modified))),
        deleted=tuple(sorted(set(deleted))),
        renamed=tuple(sorted(set(renamed))),
        has_uncommitted=has_uncommitted, head_commit=head_commit,
        raw_status=status.stdout)


def write_set_violations(diff: WorkspaceDiff, files_write: list[str]) -> list[str]:
    """Changed paths outside the declared write set (directory prefixes
    count as declared, matching the validation overlap rules)."""
    allowed = [p.replace("\\", "/").rstrip("/") for p in files_write]
    violations = []
    for path in diff.changed:
        norm = path.rstrip("/")
        permitted = any(
            norm == a or norm.startswith(a.rstrip("/") + "/") for a in allowed)
        if not permitted:
            violations.append(path)
    return violations


def remove_workspace(project_root: Path, team_root: Path, team: str,
                     task_id: str, *, keep_branch: bool = False,
                     attempt: int = 1) -> None:
    """Remove the worktree and (optionally) its branches after integration."""
    target = workspace_dir(team_root, task_id, attempt)
    if target.exists():
        _git(["worktree", "remove", "--force", str(target)], project_root)
    if not keep_branch:
        # all attempt branches of the task
        proc = subprocess.run(
            ["git", "for-each-ref", f"refs/heads/agent-teams/{team}/{task_id}",
             "--format=%(refname:short)"],
            cwd=str(project_root), capture_output=True, text=True, timeout=30)
        for branch in proc.stdout.split():
            subprocess.run(["git", "branch", "-D", branch],
                           cwd=str(project_root), capture_output=True,
                           text=True)


def submission_ref(team: str, task_id: str, submission_id: str) -> str:
    return f"refs/agent-teams/{team}/{task_id}/{submission_id}"


def pin_submission(project_root: Path, team: str, task_id: str,
                   submission_id: str, commit_sha: str) -> str:
    """Protect an accepted-for-verification commit from garbage collection."""
    ref = submission_ref(team, task_id, submission_id)
    _git(["update-ref", ref, commit_sha], project_root)
    return ref


def is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor,
                           descendant], cwd=str(project_root),
                          capture_output=True, text=True, timeout=30)
    return proc.returncode == 0


def head_sha(workspace: Path) -> str:
    return _git(["rev-parse", "HEAD"], workspace).stdout.strip()


def dirty_entries(workspace: Path) -> list[str]:
    """Staged, unstaged and untracked files (gitignored files excluded)."""
    status = _git(["status", "--porcelain", "-z", "--untracked-files=all"],
                  workspace)
    entries, index = [], 0
    records = [chunk for chunk in status.stdout.split("\0") if chunk]
    while index < len(records):
        record = records[index]
        path = record[3:].replace("\\", "/")
        index += 1
        if record[:2].startswith("R") or record[:2].startswith("C"):
            index += 1  # rename/copy source follows
        entries.append(path)
    return entries


def checkout_snapshot(project_root: Path, team_root: Path, task_id: str,
                      commit_sha: str) -> Path:
    """Ephemeral clean worktree at an exact SHA for verification.

    Removed by drop_snapshot after the checks; recreated on demand, so no
    per-submission directory accumulates on disk.
    """
    target = team_root / "tasks" / task_id / "verify-ws"
    _git(["worktree", "remove", "--force", str(target)], project_root,
         check=False)
    _git(["worktree", "add", "--detach", str(target), commit_sha],
         project_root)
    return target


def drop_snapshot(project_root: Path, team_root: Path, task_id: str) -> None:
    target = team_root / "tasks" / task_id / "verify-ws"
    _git(["worktree", "remove", "--force", str(target)], project_root,
         check=False)


def merge_sha(workspace: Path, sha: str) -> subprocess.CompletedProcess:
    return _git(["merge", "--no-edit", sha], workspace, check=False)


def list_worktrees(project_root: Path) -> list[str]:
    proc = _git(["worktree", "list", "--porcelain"], project_root)
    return [line[len("worktree "):] for line in proc.stdout.splitlines()
            if line.startswith("worktree ")]
