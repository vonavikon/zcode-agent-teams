# Workspace isolation

Modes (`config.workspace`): `auto` (default: worktree when the project is
a git repository, otherwise shared with a warning), `worktree` (required:
fails on non-git projects), `shared` (no isolation, warned).

Each task gets `agent-teams/<team>/<task>` branch and a worktree under
`.agent-teams/<team>/tasks/<task>/workspace`, with the base commit
recorded on the task row.

Lifecycle:

- claim creates the worktree (or reuses an existing one: after a crash,
  a failed claim or a release, uncommitted work survives),
- submit captures the actual diff versus the base commit (committed,
  staged, unstaged and untracked files) and compares it against
  `files_write`; violations are recorded on the task, logged as
  `workspace_conflict` events and force a quality-gate FAIL,
- blocked tasks keep their worktrees (intermediate work is not lost),
- `workspace --clean <task>` removes the worktree of a completed task
  while keeping its branch; the integration gate consumes the branches.

A worktree isolates work between tasks; the write-set comparison is what
restricts an executor to the declared files. Both are needed.
