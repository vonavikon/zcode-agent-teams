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


@dataclass(frozen=True)
class CompositionConflict:
    """A commit that could not be merged into a composition.

    ``incoming`` is that commit, ``files`` the paths that no merge could
    reconcile.
    """
    incoming: str
    files: tuple[str, ...]


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
    """Create the per-attempt worktree of a task; returns its info.

    The worktree sits on ``base_commit`` and so does its branch when the
    branch had to be created. An existing branch that was kept on purpose
    (its worktree was removed, the branch was not) is checked out as it
    is.
    """
    if not is_git_project(project_root):
        raise ExternalError(
            f"no git repository at {project_root}: configure workspace "
            "'shared' or run outside git")
    target = workspace_dir(team_root, task_id, attempt)
    branch = branch_name(team, task_id, attempt)
    if base_commit is None:
        base_commit = _git(["rev-parse", "HEAD"], project_root).stdout.strip()
    orphaned = False
    if target.exists() and (target / ".git").exists():
        # An existing worktree is reused (recovery after a crash). It was
        # made by a claim that did not get through, so it was never handed
        # out. It is the workspace of this attempt only where it sits on
        # the base asked for now; one left on another base (a claim the
        # engine refused, an engine that chose another base) is rebuilt,
        # and with it its branch: both are leftovers of that same claim.
        if head_sha(target) == base_commit:
            return WorkspaceInfo(str(target), branch, base_commit)
        _git(["worktree", "remove", "--force", str(target)], project_root)
        orphaned = True
    target.parent.mkdir(parents=True, exist_ok=True)
    if _branch_exists(project_root, branch) and not orphaned:
        _git(["worktree", "add", str(target), branch], project_root)
    else:
        # -B creates the branch or moves the leftover one onto the base.
        _git(["worktree", "add", "-B", branch, str(target), base_commit],
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
# File modes whose content is text for the line-by-line merge.
_TEXT_MODES = ("100644", "100755")
# The header of a hunk in the output of ``git diff -U0``.
_HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


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


def judged_changes(diff: WorkspaceDiff,
                   inherited: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The paths of ``diff`` the write-set control judges: every path it
    recorded, less the source of a copy the task inherited from its
    parents and left as it was.

    ``inherited`` names the results a dependent task received from its
    parents (spec 3.3): the engine itself put them into the worktree, and
    the dependency declared them as its inputs. A copy of such a file
    read what the task was meant to read. The file is judged like any
    other as soon as it really changed: added, modified or deleted paths
    always count as changes.
    """
    really_changed = set(diff.added) | set(diff.modified) | set(diff.deleted)
    received = set(inherited) - really_changed
    excused = {source for source, _destination in diff.copies
               if source in received}
    return tuple(path for path in diff.changed if path not in excused)


def write_set_violations(diff: WorkspaceDiff, files_write: list[str],
                         inherited: tuple[str, ...] = ()) -> list[str]:
    """Changed paths outside the declared write set (directory prefixes
    count as declared, matching the validation overlap rules).

    Every path is judged on its own, so a rename or a copy violates the
    set when either end lies outside it: a file moved into the allowed
    zone from a forbidden one, or copied from it, is caught by its source.
    ``inherited`` goes to :func:`judged_changes`.
    """
    allowed = [p.replace("\\", "/").rstrip("/") for p in files_write]
    touched = set(judged_changes(diff, inherited))
    touched.update(path for pair in diff.renames for path in pair)
    touched.update(destination for _source, destination in diff.copies)
    violations = []
    for path in sorted(touched):
        norm = path.rstrip("/")
        permitted = any(norm == a or norm.startswith(a + "/") for a in allowed)
        if not permitted:
            violations.append(path)
    return violations


def changed_paths(project_root: Path, older: str, newer: str) -> tuple[str, ...]:
    """Paths that differ between two commits, POSIX-relative to the
    repository root. Empty when ``older`` is not an ancestor of
    ``newer``: they are not one history then, and nothing was added to
    the older one."""
    if not is_ancestor(project_root, older, newer):
        return ()
    raw = _git_z(["diff", "--name-only", "-z", older, newer], project_root)
    return tuple(sorted(path.replace("\\", "/") for path in _nul_fields(raw)))


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


# ---------------------------------------------------------- dependency base


def compose_dir(team_root: Path, task_id: str) -> Path:
    """The scratch worktree the dependency base of a task is built in."""
    return team_root / "tasks" / task_id / "compose-ws"


def compose_commits(project_root: Path, team_root: Path, task_id: str,
                    commits: list[str], message: str
                    ) -> str | CompositionConflict | None:
    """The one commit that contains every commit of ``commits``.

    This is the dependency base of a task (spec 3.3): the accepted
    commits of its parents, merged so that a worktree grown from the
    result holds the results of all of them. The first commit is the
    starting point and every next one is merged into the composition so
    far, in the order given. A commit the composition already contains is
    skipped; a commit that contains the composition moves it forward.

    The merges run in a scratch worktree that is removed before this
    returns, whatever happened. The project checkout, the branches and
    the worktrees of the tasks are not touched, and nothing of the
    composition is kept when the parents conflict.

    What git reports as a conflict gets one line-by-line pass
    (:func:`merge_lines`), because git also conflicts on changes that
    merely touch: two parents that changed different lines of one file
    have to compose. A line both sides changed to different content
    stays a conflict. It comes back as a :class:`CompositionConflict`
    and the caller names the parents.

    Returns the commit, the conflict, or None when ``commits`` is empty.
    """
    current: str | None = None
    scratch = compose_dir(team_root, task_id)
    opened = False
    try:
        for sha in commits:
            if current is None:
                current = sha
            elif is_ancestor(project_root, sha, current):
                continue
            elif not opened and is_ancestor(project_root, current, sha):
                current = sha
            else:
                if not opened:
                    scratch.parent.mkdir(parents=True, exist_ok=True)
                    _git(["worktree", "remove", "--force", str(scratch)],
                         project_root, check=False)
                    _git(["worktree", "add", "--detach", str(scratch), current],
                         project_root)
                    opened = True
                left = _merge_into(project_root, scratch, sha, message)
                if left:
                    return CompositionConflict(sha, tuple(left))
                current = head_sha(scratch)
    finally:
        if opened:
            _git(["worktree", "remove", "--force", str(scratch)],
                 project_root, check=False)
    return current


def _merge_into(project_root: Path, scratch: Path, sha: str, message: str
                ) -> tuple[str, ...] | None:
    """Merge ``sha`` into the composition checked out in ``scratch``.

    Returns the paths that stayed unmerged, or None when the merge went
    through (by git, or by the line-by-line pass after git).
    """
    proc = _git(["merge", "--no-edit", "-m", message, sha], scratch,
                check=False)
    if proc.returncode == 0:
        return None
    unmerged = _unmerged_entries(scratch)
    left = _resolve_unmerged(project_root, scratch, unmerged)
    if unmerged and not left:
        # Every conflict was reconciled line by line: complete the merge.
        # The files on disk still show the conflict markers. They are
        # thrown away with the worktree, but the merge of a next parent
        # has to see the committed content.
        _git(["commit", "-m", message], scratch)
        _git(["reset", "--hard", "HEAD"], scratch)
        return None
    # A merge that did not go through leaves nothing behind.
    _git(["merge", "--abort"], scratch, check=False)
    if not unmerged:
        # Nothing was in conflict, so this is not a merge problem (a
        # refused hook, a missing identity, ...): git's words are the clue.
        raise ExternalError(
            f"git merge {sha[:12]} failed in {scratch}: "
            f"{proc.stderr.strip()[:300]}")
    return left


def _unmerged_entries(scratch: Path) -> dict[str, dict[int, tuple[str, str]]]:
    """``{path: {stage: (mode, blob)}}`` of the unmerged entries of the
    index: stage 1 is the common ancestor, 2 the composition so far, 3
    the commit being merged."""
    proc = subprocess.run(["git", "ls-files", "-u", "-z"], cwd=str(scratch),
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise ExternalError(
            f"git ls-files -u failed in {scratch}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    unmerged: dict[str, dict[int, tuple[str, str]]] = {}
    for record in proc.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not record:
            continue
        info, _sep, path = record.partition("\t")
        mode, blob, stage = info.split(" ")
        unmerged.setdefault(path, {})[int(stage)] = (mode, blob)
    return unmerged


def _resolve_unmerged(project_root: Path, scratch: Path,
                      unmerged: dict[str, dict[int, tuple[str, str]]]
                      ) -> list[str]:
    """Replace every unmerged entry that :func:`merge_lines` can merge by
    its result, right in the index of ``scratch`` (the files on disk keep
    their conflict markers; the worktree is thrown away). Returns the
    paths it could not merge; nothing is replaced unless all of them can
    be.
    """
    merged = {path: _merged_content(project_root, entries)
              for path, entries in unmerged.items()}
    left = sorted(path for path, content in merged.items() if content is None)
    if left:
        return left
    for path, content in merged.items():
        mode = unmerged[path][2][0]
        blob = _write_blob(project_root, content)
        _git(["update-index", "--cacheinfo", f"{mode},{blob},{path}"], scratch)
    return []


def _merged_content(project_root: Path,
                    entries: dict[int, tuple[str, str]]) -> bytes | None:
    """The merged content of one unmerged path, or None when only a human
    can merge it: not a conflict of file contents on all three stages
    (add/add, modify/delete, a mode or a symlink), a binary file, or
    lines that both sides changed to different content.
    """
    if set(entries) != {1, 2, 3}:
        return None
    modes = {entries[stage][0] for stage in entries}
    if len(modes) != 1 or entries[2][0] not in _TEXT_MODES:
        return None
    base, ours, theirs = (_blob(project_root, entries[stage][1])
                          for stage in (1, 2, 3))
    if any(b"\0" in data for data in (base, ours, theirs)):
        return None
    base_lines, ours_lines, theirs_lines = (_lines_of(data)
                                            for data in (base, ours, theirs))
    merged, clean = merge_lines(
        base_lines,
        _change_regions(project_root, entries[1][1], entries[2][1],
                        ours_lines),
        _change_regions(project_root, entries[1][1], entries[3][1],
                        theirs_lines))
    return b"".join(merged) if clean else None


def merge_lines(base: list[bytes],
                ours: list[tuple[int, int, list[bytes]]],
                theirs: list[tuple[int, int, list[bytes]]]
                ) -> tuple[list[bytes], bool]:
    """Line-by-line three-way merge of the changes of two sides.

    ``ours`` and ``theirs`` list what each side changed against ``base``
    (see :func:`_change_regions`): ``(start, end, lines)`` over the line
    numbers of ``base``, where ``base[start:end]`` became ``lines`` and
    an empty range is an insertion before line ``start``.

    A base range changed by one side only is changed as that side wants
    it; changed by both it is taken once when both made the same change
    and is a conflict otherwise. Two insertions at one point are the same
    change when their lines are equal. Returns the merged lines and
    whether the sides agree.
    """
    out: list[bytes] = []
    pos, a, b = 0, 0, 0
    while a < len(ours) or b < len(theirs):
        ours_next = ours[a] if a < len(ours) else None
        theirs_next = theirs[b] if b < len(theirs) else None
        if ours_next is not None and theirs_next is not None and (
                ours_next[0] == ours_next[1] == theirs_next[0] == theirs_next[1]):
            # both insert at one point
            out += base[pos:ours_next[0]]
            if ours_next[2] != theirs_next[2]:
                return out, False
            out += ours_next[2]
            pos, a, b = ours_next[1], a + 1, b + 1
        elif ours_next is not None and (theirs_next is None
                                        or ours_next[1] <= theirs_next[0]):
            out += base[pos:ours_next[0]] + ours_next[2]
            pos, a = ours_next[1], a + 1
        elif theirs_next is not None and (ours_next is None
                                          or theirs_next[1] <= ours_next[0]):
            out += base[pos:theirs_next[0]] + theirs_next[2]
            pos, b = theirs_next[1], b + 1
        else:
            # one base range changed by both sides
            out += base[pos:ours_next[0]]
            if ours_next != theirs_next:
                return out, False
            out += ours_next[2]
            pos, a, b = ours_next[1], a + 1, b + 1
    return out + base[pos:], True


def _change_regions(project_root: Path, base: str, side: str,
                    side_lines: list[bytes]) -> list[tuple[int, int, list[bytes]]]:
    """What one side changed against the base, as git reports it: every
    hunk of ``git diff -U0`` between the two blobs as ``(start, end,
    lines)``.

    A hunk that puts as many lines as it takes away is one change per
    line: a side that rewrote two lines made two edits, and each is
    judged on its own by :func:`merge_lines`.
    """
    proc = subprocess.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
         "-U0", base, side], cwd=str(project_root), capture_output=True,
        timeout=120)
    if proc.returncode != 0:
        raise ExternalError(
            f"git diff {base[:12]} {side[:12]} failed in {project_root}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    regions: list[tuple[int, int, list[bytes]]] = []
    for line in proc.stdout.split(b"\n"):
        header = _HUNK_HEADER.match(line)
        if header is None:
            continue
        old_at, old_count, new_at, new_count = [
            int(part) if part is not None else 1 for part in header.groups()]
        start = old_at if old_count == 0 else old_at - 1
        new_from = new_at if new_count == 0 else new_at - 1
        lines = side_lines[new_from:new_from + new_count]
        if old_count > 1 and old_count == len(lines):
            regions.extend((start + offset, start + offset + 1, [text])
                           for offset, text in enumerate(lines))
        else:
            regions.append((start, start + old_count, lines))
    return regions


def _lines_of(data: bytes) -> list[bytes]:
    """``data`` cut at newlines, every line keeping its newline; a last
    line without one stays as it is. A carriage return is part of its
    line, which is how git counts lines too."""
    lines = data.split(b"\n")
    tail = lines.pop()  # empty when the data ends with a newline
    return [line + b"\n" for line in lines] + ([tail] if tail else [])


def _blob(project_root: Path, sha: str) -> bytes:
    proc = subprocess.run(["git", "cat-file", "blob", sha],
                          cwd=str(project_root), capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise ExternalError(
            f"git cat-file {sha[:12]} failed in {project_root}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout


def _write_blob(project_root: Path, content: bytes) -> str:
    """Store ``content`` as a blob, as it is (no filters); returns its SHA."""
    proc = subprocess.run(["git", "hash-object", "-w", "--stdin"],
                          cwd=str(project_root), input=content,
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise ExternalError(
            f"git hash-object failed in {project_root}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout.decode("utf-8", "replace").strip()


def merge_sha(workspace: Path, sha: str) -> subprocess.CompletedProcess:
    return _git(["merge", "--no-edit", sha], workspace, check=False)


def list_worktrees(project_root: Path) -> list[str]:
    proc = _git(["worktree", "list", "--porcelain"], project_root)
    return [line[len("worktree "):] for line in proc.stdout.splitlines()
            if line.startswith("worktree ")]
