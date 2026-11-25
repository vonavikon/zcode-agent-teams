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

import re
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
    """What differs between the base commit and the worktree.

    Paths are POSIX-relative to the workspace root. ``changed`` holds
    every path the write-set control has to judge, so a rename or a copy
    puts both of its paths there; ``renames`` and ``copies`` keep them as
    ``(source, destination)`` pairs.
    """

    changed: tuple[str, ...]
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    renames: tuple[tuple[str, str], ...]
    copies: tuple[tuple[str, str], ...]
    has_uncommitted: bool
    head_commit: str | None
    raw_status: str

    @property
    def renamed(self) -> tuple[str, ...]:
        """Destination paths of the renames: the flat view that the field
        of this name used to hold before the pairs were recorded."""
        destinations = {destination for _source, destination in self.renames}
        return tuple(sorted(destinations))


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


def _git_z(args: list[str], cwd: Path) -> str:
    """stdout of a git command as NUL-terminated text.

    The bytes are decoded as UTF-8 whatever the locale is and without
    newline translation: -z makes git print names verbatim, and no path
    may be cut, joined or re-escaped on its way in. A name that is not
    valid UTF-8 keeps a readable escaped form instead of failing.
    """
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          timeout=120)
    if proc.returncode != 0:
        raise ExternalError(
            f"git {' '.join(args[:2])} failed in {cwd}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout.decode("utf-8", "backslashreplace")


def _git_status(workspace: Path) -> str:
    """``git status --porcelain -z`` of a worktree, untracked files
    listed one by one."""
    return _git_z(["status", "--porcelain", "-z", "--untracked-files=all"],
                  workspace)


def _nul_fields(raw: str) -> list[str]:
    """The NUL-terminated fields of a ``git -z`` output, verbatim.

    A stream that does not end with its terminator was cut short; its
    last field would be lost or read whole, so the stream is rejected.
    """
    if not raw:
        return []
    if not raw.endswith("\0"):
        raise ExternalError(
            "truncated git -z output: the last record is not terminated")
    return raw.split("\0")[:-1]


_NAME_STATUS = re.compile(r"[ACDMRTUXB][0-9]{0,3}")


def parse_name_status_z(raw: str) -> list[tuple[str, str, str | None]]:
    """Records of ``git diff --name-status -z`` as ``(status, path,
    other)``: ``other`` is the second path of a rename or a copy and None
    for any other record.

    A record is STATUS, path. A rename or a copy has three fields: STATUS,
    source, destination, the source first and the destination second,
    exactly as in the quoted format without -z.
    """
    fields = _nul_fields(raw)
    records, index = [], 0
    while index < len(fields):
        status = fields[index]
        paired = status[:1] in ("R", "C")
        size = 3 if paired else 2
        if not _NAME_STATUS.fullmatch(status) or index + size > len(fields):
            raise ExternalError(
                f"malformed git diff --name-status record: {status[:80]!r}")
        records.append((status, fields[index + 1],
                        fields[index + 2] if paired else None))
        index += size
    return records


def parse_status_z(raw: str) -> list[tuple[str, str, str | None]]:
    """Records of ``git status --porcelain -z`` as ``(XY, path, origin)``:
    ``origin`` is the path a renamed or copied file came from and None
    for any other record.

    A record is ``XY PATH``, the index and the worktree status letters in
    front. A rename or a copy (X is R or C) is followed by one more field,
    the source path: the destination comes first and the source AFTER it,
    the reverse of ``git diff``.
    """
    fields = _nul_fields(raw)
    records, index = [], 0
    while index < len(fields):
        record = fields[index]  # "XY " in front of a non-empty path
        index += 1
        if len(record) < 4 or record[2] != " ":
            raise ExternalError(
                f"malformed git status --porcelain record: {record[:80]!r}")
        paired = record[0] in ("R", "C")
        if paired and index >= len(fields):
            raise ExternalError(
                "truncated git status --porcelain record: the source path of "
                f"{record[3:83]!r} is missing")
        records.append((record[:2], record[3:],
                        fields[index] if paired else None))
        if paired:
            index += 1
    return records


def capture_diff(workspace: Path, base_commit: str) -> WorkspaceDiff:
    """Changed files of a worktree versus its base commit.

    Covers committed changes (base..HEAD), staged and unstaged work, and
    untracked files; paths are POSIX-relative to the workspace root.

    A rename or a copy is recorded with both of its paths and both end up
    in ``changed``: a file moved out of a forbidden location changes that
    location as surely as a write would.
    """
    added: set[str] = set()
    modified: set[str] = set()
    deleted: set[str] = set()
    renames: set[tuple[str, str]] = set()
    copies: set[tuple[str, str]] = set()

    def posix(path: str) -> str:
        return path.replace("\\", "/")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(workspace),
                          capture_output=True, text=True, timeout=30)
    head_commit = head.stdout.strip() if head.returncode == 0 else None

    # Committed, staged and unstaged changes against the base. Copies are
    # looked for among unmodified files too: otherwise copying a file
    # leaves no trace but its destination.
    diff = _git_z(["diff", "--name-status", "-z", "-M", "-C",
                   "--find-copies-harder", base_commit], workspace)
    for status, path, other in parse_name_status_z(diff):
        if other is None:
            if status == "A":
                added.add(posix(path))
            elif status == "D":
                deleted.add(posix(path))
            else:
                modified.add(posix(path))
        elif status[0] == "R":
            renames.add((posix(path), posix(other)))
        else:
            copies.add((posix(path), posix(other)))

    # Untracked files and worktree-only states are visible here alone; a
    # staged rename or copy repeats its two paths.
    status_z = _git_status(workspace)
    status_records = parse_status_z(status_z)
    for xy, path, origin in status_records:
        path = posix(path)
        if origin is not None:
            pair = (posix(origin), path)
            (renames if xy[0] == "R" else copies).add(pair)
        if xy == "??":
            added.add(path)
        elif xy == "AD":
            deleted.add(path)  # added in the index, gone from disk
        elif "D" in xy:
            deleted.add(path)
        elif "A" in xy:
            added.add(path)
        elif path not in added and path not in deleted:
            modified.add(path)

    endpoints = {path for pair in renames | copies for path in pair}
    return WorkspaceDiff(
        changed=tuple(sorted(added | modified | deleted | endpoints)),
        added=tuple(sorted(added)), modified=tuple(sorted(modified)),
        deleted=tuple(sorted(deleted)), renames=tuple(sorted(renames)),
        copies=tuple(sorted(copies)),
        has_uncommitted=bool(status_records), head_commit=head_commit,
        raw_status=status_z)


def write_set_violations(diff: WorkspaceDiff, files_write: list[str]) -> list[str]:
    """Changed paths outside the declared write set (directory prefixes
    count as declared, matching the validation overlap rules).

    Every path is judged on its own, so a rename or a copy violates the
    set when either end lies outside it: a file moved into the allowed
    zone from a forbidden one, or copied from it, is caught by its source.
    """
    allowed = [p.replace("\\", "/").rstrip("/") for p in files_write]
    touched = set(diff.changed)
    pairs = diff.renames + diff.copies
    touched.update(path for pair in pairs for path in pair)
    violations = []
    for path in sorted(touched):
        norm = path.rstrip("/")
        permitted = any(norm == a or norm.startswith(a + "/") for a in allowed)
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
    """Staged, unstaged and untracked files (gitignored files excluded).

    Both paths of a staged rename or copy are listed: both differ from HEAD.
    """
    entries = []
    for _xy, path, origin in parse_status_z(_git_status(workspace)):
        entries.append(path.replace("\\", "/"))
        if origin is not None:
            entries.append(origin.replace("\\", "/"))
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
