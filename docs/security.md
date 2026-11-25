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

## The board (`team.py ui`)

- Binds to 127.0.0.1 unless `--host` says otherwise (a warning is
  printed when the board becomes reachable from other machines).
- Read-only by construction: `state.db` is opened with `mode=ro` and
  `PRAGMA query_only`; only GET and HEAD are answered; the state files
  are not written. SQLite itself may create its empty `-shm` and `-wal`
  sidecars next to the database: bookkeeping, not team state.
- The project root is fixed by the command that started the server. A
  request names a team inside it (validated by the name rule); a
  `project_root` in the address is ignored.
- A `Host` header that names another machine is refused (403) while
  the server is bound to loopback: that is what a DNS rebinding attack
  looks like.
- The payload comes from a whitelist of columns; the lead token hash
  and the execution token hashes are never read. In the `simple` level
  no agent id leaves the server, only role names.
- The page's CSP allows nothing inline and nothing remote: no CDN, no
  beacon, no inline script or style. Every value from the wire is
  escaped, or forced to a number, before it reaches the markup; this
  is pinned by tests that run the page script on a hostile snapshot.
- `/api/results/<task>/<file>` answers only with a file that the
  task's latest hand-over consists of (a list the engine recorded), read
  as that hand-over pinned it (`git show <sha>:<path>`, a read-only
  command) or, in shared mode, from the project folder; the name is
  checked against path traversal. Text is served as `text/plain` under
  `default-src 'none'; sandbox`, never as HTML: a result is the team's
  own content and cannot act as a page of the board. Pictures are
  served as pictures, everything else as an attachment; each kind has a
  size limit.
- Not a boundary: the board has no authentication. Whoever can reach
  the address reads what the lead reads (titles, files, results,
  remarks).

## NOT a security boundary (documented limitation)

The engine is a local CLI. An agent with arbitrary shell and filesystem
access can read `state.db`, edit files directly or kill processes. The
tokens protect the workflow against mistakes and confusion between
roles, not against a hostile executor with full machine access. Strong
isolation needs a trusted coordinator service outside the workspace.

## Residual risks

- Verification reports are validated structurally and bound to the
  pinned submission, but the engine cannot cryptographically prove
  an independent agent wrote them; lead overrides are audited
  events, not proofs.

- `files_read` is a declaration for the verifier's checks; reads are not
  technically restricted.
- In `shared` workspace mode (non-git projects) write isolation is absent
  and stated so at init.
- The price table for cost is a fallback estimate, flagged as such.
