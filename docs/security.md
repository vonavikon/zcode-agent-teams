# Security model

## Enforced by the engine

- Name validation: team, agent and task ids match
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` (path traversal guard).
- Path rules in specs: no absolute paths, no `..`, no reserved engine
  directories, symlink escapes detected, case collisions warned about.
- Write-set control (git projects): the actual diff of the worktree is
  compared against `files_write`; violations are recorded and force a
  FAIL of the quality gate.
- Authority: the lead token is printed once by `init` and stored as a
  sha256 hash; the execution token is printed once by `claim` and checked
  on `submit`/`heartbeat`. A `--by verifier` CLI value is an actor record
  and never grants anything.
- Machine gate: required command-backed criteria run in the task
  workspace; a failure overrides a PASS verdict.
- Audit: hash-chained event log, `verify_chain` detects any later row
  edit; verification attempts store the full criterion report.

## NOT a security boundary (documented limitation)

The engine is a local CLI. An agent with arbitrary shell and filesystem
access can read `state.db`, edit files directly or kill processes. The
tokens protect the workflow against mistakes and confusion between
roles, not against a hostile executor with full machine access. Strong
isolation needs a trusted coordinator service outside the workspace.

## Residual risks

- `files_read` is a declaration for the verifier's checks; reads are not
  technically restricted.
- In `shared` workspace mode (non-git projects) write isolation is absent
  and stated so at init.
- The price table for cost is a fallback estimate, flagged as such.
