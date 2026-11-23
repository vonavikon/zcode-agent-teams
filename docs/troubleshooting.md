# Troubleshooting

- `team not found ... (run init first)` - the team directory has no
  `state.db`; for teams created by the v0.2 engine use `team_v02.py`.
- `state lock is not reentrant` - the same process holds the lock for
  that team already; do not nest engine calls in one process.
- `lock not acquired within Ns; holder: ...` - another process holds the
  state lock. The message names the owner, pid and age. The OS releases
  the lock when that process exits; do not delete the lock file.
- `invalid or stale execution token` - submit/heartbeat needs the token
  printed by the CURRENT claim; after a release and a new claim the old
  token is void by design.
- `this command requires --lead-token` / `invalid lead token` - the lead
  token is printed once by init and stored hashed; if lost, there is no
  recovery path by design: start a new run or a new team.
- `task ... is blocked` - the verification limit was hit: `release`
  (respecting the execution retry limit), fix manually, or `cancel`.
- `cannot integrate: tasks not completed` - finish or cancel every task
  first.
- Integration conflicts: the conflicting files are listed; resolve them
  in a task branch (or rework the overlap) and integrate again. Task
  branches and the project branch are never touched by a failed gate.
- `database ... newer engine version` - the team was created by a newer
  engine; upgrade the plugin.
- `no git repository` with workspace `worktree` - switch the config to
  `auto` or `shared`, or init the repository.
- SQLite `database is locked` - another engine process is mid-transaction
  on a different lock path; the busy timeout is 10 s, retries are safe:
  every command is idempotent at the state level.
