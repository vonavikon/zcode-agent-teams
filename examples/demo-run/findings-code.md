# team.py review: correctness lens (init/claim/verify/message/read/status)

File: `plugins/agent-teams/skills/agent-teams/scripts/team.py` (357 lines). All line numbers and quotes were verified by reading the file. There is no CRITICAL section: on the happy path (one agent, sequential commands) the logic produces no errors; the heaviest problems are races and unrecoverable states after crashes.

## CRITICAL

Not found.

## MAJOR

### M1. Read-modify-write race on tasks.json: statuses of neighboring tasks get lost
team.py:114, 136 (claim); 146, 154, 160 (verify); 47-50 (save_tasks)

`cmd_claim` and `cmd_verify` follow the pattern "read tasks.json at the start, write it at the end", with no locking between the read and the write. Scenario: agent A and agent B claim different tasks at the same time. Both manage to run `load_tasks` (line 114): both see all tasks as `pending`. A creates its lock and saves (`save_tasks`, 136): task A -> `in_progress`. B creates its own lock and saves its own copy of `data` (136): task A silently rolls back to `pending`, task B -> `in_progress`. Result: in tasks.json task A = `pending`, but A's lock file already exists. Claiming it again is impossible (line 125, "locked by another agent"), and so is verify (line 148, "not in progress"). The task is stuck for good without manual editing. The demo scenario starts three agents in parallel, so the window between 114 and 136 is really reachable.

Fix: wrap every command that changes tasks.json in a lock such as `locks/.tasks.lock` (O_CREAT|O_EXCL with a short retry loop, released in a finally), or in `save_tasks` re-read the file before writing and carry over only the changes of the command's own task (a merge) instead of writing the whole snapshot.

### M2. Race on the mailbox files: lost messages, including verify fail feedback
team.py:163-170 (verify fail), 180-186 (message), 199-202 (read)

All three places do "read the mailbox, append or mark, write the whole file back" without a lock. Two simultaneous `message --to X` calls (for example, the verifier sends a VERIFY FAIL while the lead sends a normal message): the last write survives, the first message is lost silently. It is worse with `cmd_read` (199-202): if someone appends a message between the read (198) and the write (202), it is overwritten by the old snapshot and lost together with the delivery attempt. Losing a VERIFY FAIL breaks the "round 2" loop from the executor's prompt: it simply never learns about the failure.

Fix: the same per-file lock as in M1 (for example `locks/inbox-<agent>.lock`), or one command-level lock around any mailbox change. At minimum, `cmd_read` should re-read the file before writing and merge the messages that appeared after the read.

### M3. Orphaned lock: a crash between os.open and save_tasks blocks the task forever
team.py:122-136

The lock is created on line 124, the status in tasks.json changes only on 136. A crash (Ctrl+C, disk, killing the agent) between those points leaves: the lock exists, the status is `pending`. A repeated claim by the same agent fails at 125-132 with "locked by another agent: <its own name>". The script has no command to remove a lock: grep for unlink/remove/rmtree comes up empty, and argparse (304-349) only has init/claim/verify/message/read/status/langfuse. Recovery is only by deleting the file by hand.

Fix: add a subcommand `unlock --team --task --by` (removes the lock when `--by` matches the owner recorded in the lock file, plus `--force` for a human). Additionally the claim owner can be written into tasks.json (a `claim_owner` field): a divergence between the lock and the status then becomes much easier to notice.

### M4. Team, agent and task names are not checked: escaping the team directory and OSError on Windows
team.py:98 (init, team path), 122 (lock file name), 163, 180, 194 (mailbox file name)

`--team`, `--to`, `--for`, `--by` and `--task` go into paths as is. `--to "../evil"` writes `inbox/../evil.json`, that is `<team>/evil.json`, outside inbox; `--team "../x"` at init creates a directory outside `.agent-teams`. The invariant "all files of a team live in `.agent-teams/<team>/`" is not enforced anywhere. Separately: a name with the characters `<>:"|?*` on Windows fails with an unreadable OSError traceback instead of a clean error.

Fix: validate all names with one regular expression at the start of every command (or through `type=` in argparse): `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, and `sys.exit` with a clear message on a violation.

## MINOR

### m1. The verifying status is never assigned: a dead state
team.py:148

Verify accepts the statuses `("in_progress", "verifying")`, but the file only ever assigns `pending` (92), `in_progress` (135) and `completed` (153). Nothing ever moves a task into `verifying`: grep confirms the single occurrence of the word at 148. Either the state was forgotten in the implementation (for example a submit subcommand before verify), or it was dropped from the design while the check stayed.

Fix: remove `"verifying"` from the tuple at 148, or add a transition into it (for example `claim` by the same owner again, or a separate `submit` command).

### m2. Claim does not check --by against the task assignee
team.py:133-138, 163

`--by` is free text, the engine does not check it against `t["assignee"]`. Any agent can claim someone else's task under any name; on a verify fail the feedback goes to the mailbox of the formal assignee (163), not the actual executor, and the fix loop stalls.

Fix: in `cmd_claim` a check `if args.by != t["assignee"]: sys.exit(...)` (with a `--force` flag for reassignment if needed).

### m3. The mailbox is not written atomically, unlike tasks.json
team.py:170, 186, 202 versus 47-50

`save_tasks` carefully writes through tmp + `replace`, while all three mailbox writes use plain `write_text`. A crash in the middle of a write leaves broken JSON, after which `read` (198), `message` (181) and `status` (218) fail with JSONDecodeError forever, until a manual repair.

Fix: extract `save_json_atomic(path, obj)` (tmp + `Path.replace`) and use it in the six write places: 170, 186, 202 and 49-50.

### m4. cmd_status fails as a whole because of one broken mailbox file
team.py:218-219

The loop over `inbox/*.json` calls `json.loads` with no error handling. One damaged or foreign .json (see m3 and M4) breaks the whole status output: not even the task list is visible.

Fix: wrap the reading of each file in try/except and print `mailbox <name>: file is corrupt` instead of failing.

### m5. A partially created team directory gives contradictory hints
team.py:99-103, 38-39

If init fails after `mkdir` (101-102) but before `save_tasks` (103), a directory without tasks.json remains. Then init says "team directory already exists" (100) while claim says "team not found... (run init first)" (39). Both commands are right in their own way, and the only way out is deleting the directory by hand.

Fix: in init check not `d.exists()` but `(d / "tasks.json").exists()` (as `team_dir` does), and when a directory exists without tasks.json either complete the structure or report "found a partial directory, remove it".

### m6. --tasks is resolved against the CWD, not against --project-root
team.py:71-72

`root` comes from `--project-root`, while `src = Path(args.tasks).resolve()` resolves against the current directory of the process. The other commands were given `--project-root` on purpose, to be independent of the CWD; init partly falls out of that scheme: run from a foreign directory with a relative path to the spec, it fails with a FileNotFoundError traceback.

Fix: `src = Path(args.tasks); src = src if src.is_absolute() else root / src`, or at least a clean error "tasks file not found: <path>".

### m7. deps are not checked for existence at init
team.py:76-97, 118-121

A typo in `deps` ("lens-codee") surfaces only when someone claims the task: `find_task` (119) ends the process with "task not found". The invariant "deps reference existing ids" is checked at the latest possible moment.

Fix: in init, after building `seen`, check `set(t["deps"]) <= seen` for every task and complain immediately with the list of broken references.
