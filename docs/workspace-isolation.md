# Workspace isolation

Modes (`config.workspace`): `auto` (default: worktree when the project is
a git repository, otherwise shared with a warning), `worktree` (required:
fails on non-git projects), `shared` (no isolation, warned).

Each task gets `agent-teams/<team>/<task>` branch and a worktree under
`.agent-teams/<team>/tasks/<task>/workspace`, with the base commit
recorded on the task row.

Every execution attempt gets its own directory
`tasks/<task>/attempts/<n>/workspace` and branch `a<n>`, grown from
the last verified submission or the run base. A fenced attempt moves
to `tasks/<task>/quarantine/` and stays inspectable for manual
salvage. A rename or a copy is checked on both of its paths: moving a file out of
a forbidden location is a violation. A task with `deps` starts from the
accepted results of its parents (see docs/verification.md and SPEC.md
section 7); conflicting parents are rejected at claim. A failed attempt
leaves a draft a new attempt may continue from, but changes are always
judged against the trusted base (the run base or the last accepted
result), so a violation cannot be hidden by a retry.
Submit requires a clean worktree (uncommitted files are
rejected with the list) and pins an immutable snapshot ref.

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
