# Troubleshooting

- `team not found ... (run init first)` - the team directory has no
  `state.db`; for teams created by the v0.2 engine use `team_v02.py`.
- `state lock is not reentrant` - the same process holds the lock for
  that team already; do not nest engine calls in one process.
- `lock not acquired within Ns; holder: ...` - another process holds the
  state lock. The message names the owner, pid and age. The OS releases
  the lock when that process exits; do not delete the lock file.
- `error: argument --lead-token: expected one argument` - the token of
  this team starts with a dash (possible for teams created before
  2.1.0a3, once in 64). Write it glued to the option:
  `--lead-token=<token>`. Since 2.1.0a3 the command accepts both forms,
  and new tokens never start with a dash.
- `invalid or stale execution token` - submit/heartbeat needs the token
  printed by the CURRENT claim; after a release and a new claim the old
  token is void by design.
- `this command requires --lead-token` / `invalid lead token` - the lead
  token is printed once by init and stored hashed; if lost, there is no
  recovery path by design: start a new run or a new team.
- `SUBMIT_REJECTED_DIRTY_WORKTREE` - commit your work, then resubmit;
  the message lists the files. `SUBMIT_REJECTED_DETACHED_HEAD` - your
  HEAD left the task's base; redo the work on top of it.
- `VERIFICATION_REPORT_MISSING` - a PASS needs `--report` or an
  audited `--override-reason`.
- `APPLY_REJECTED_DIRTY_PROJECT` / `APPLY_REJECTED_BASE_MOVED` -
  clean or re-integrate against the new base, then apply again.
- `task ... is blocked` - the verification limit was hit: `release`
  (respecting the execution retry limit), fix manually, or `cancel`.
- `cannot integrate: tasks not completed` - finish or cancel every task
  first.
- Integration conflicts: the conflicting files are listed; resolve them
  in a task branch (or rework the overlap) and integrate again. Task
  branches and the project branch are never touched by a failed gate.
- `team.py ui`: `port ... is already in use` - another board (or another
  program) holds the port: pass `--port`, or `--port 0` for any free
  one. `team ... has no state ...; run init first` - the board needs a
  team that exists.
- The board says the connection is lost - the `team.py ui` process is
  not running any more: restart it; the page keeps the last state it
  saw and says so. `unknown Host` - open the address exactly as it was
  printed (`127.0.0.1`), not through another name.
- `database ... newer engine version` - the team was created by a newer
  engine; upgrade the plugin.
- `no git repository` with workspace `worktree` - switch the config to
  `auto` or `shared`, or init the repository.
- SQLite `database is locked` - another engine process is mid-transaction
  on a different lock path; the busy timeout is 10 s, retries are safe:
  every command is idempotent at the state level.
