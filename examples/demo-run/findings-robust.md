# Robustness review: team.py + SKILL.md (pitfalls)

Lens: Windows / Git Bash, concurrent access, command edge cases.

File paths (short names below):
- team.py = C:/Repos/zcode-agent-teams/plugins/agent-teams/skills/agent-teams/scripts/team.py
- SKILL.md = C:/Repos/zcode-agent-teams/plugins/agent-teams/skills/agent-teams/SKILL.md

A check of the "Pitfalls" section (SKILL.md:72-78) against the code is at the end, after the findings.

## CRITICAL

### R-1. tasks.json: a read-modify-write of the whole file without inter-process locking, and a tmp file with a fixed name

team.py:43-50 (load_tasks/save_tasks), the mutations in claim team.py:114->136 and verify team.py:146->154/160.

The lock in locks/ (team.py:122-134) only protects a repeated claim of the same task. The save itself, loading all of tasks.json, changing it, writing the whole file back, is not serialized by anything, while parallel claims of different tasks are the normal mode: the lead spawns teammates simultaneously (SKILL.md:23).

Loss scenario: agent A (claim task-1) and agent B (claim task-2) read the same snapshot at team.py:114; B saves first (task-2=in_progress); A saves its own snapshot and the status of task-2 rolls back to pending, B's claim is erased. Meanwhile events.jsonl contains task_claimed for both: the log and the state diverge. Nobody gets any error message.

The second half: the tmp file is one for everybody (team.py:48, `tasks.json.tmp`). Two parallel save_tasks calls write into the same tmp, so an interleaving of contents before the replace is possible, or a FileNotFoundError when the first process has already moved the file and the second one calls replace.

Fix:
1. A unique tmp name: `tmp = d / f"tasks.json.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"`.
2. Serialize the mutations: a command lock (for example `locks/.tasks.lock` through os.open with O_CREAT|O_EXCL, a spin retry with a 5-10 s timeout) taken in claim/verify/message/read around the load->save pair. On Windows an O_EXCL spin is enough, msvcrt.locking is not required.

## MAJOR

### R-2. Mailbox: read-modify-write races silently lose messages, including VERIFY FAIL feedback

team.py:163-170 (verify fail appends to the executor's box), team.py:181-186 (message), team.py:198-202 (read: load, mark read, write).

Loss scenario: an agent runs read and has loaded the snapshot (team.py:198); in parallel a verify fail appends a message and saves (team.py:170); the agent saves its own copy (team.py:202) and the verifier's message is gone. The executor never learns about the FAIL and hangs waiting: the acceptance loop breaks with no diagnostics.

SKILL.md:77 warns "the mailbox is read-modify-write without locks: do not let two agents write into the same box at the same time, usually the lead sends". The warning is incomplete: the engine itself has two writing paths, message and verify-fail (team.py:163-170). Even under the discipline "only the lead sends", a verifier with --verdict fail bypasses the rule, and a race between verify-fail and the executor's read is possible.

Fix: store the box as append-only JSONL (`inbox/<agent>.jsonl`, one line per message, appended through open("a")); keep the "read" position separately (`inbox/<agent>.read`, the ts or id of the last message read). Then read rewrites nothing and an append loses no messages. The minimal variant is to cover the inbox operations with the same command lock from R-1.

### R-3. The claim lock is never released; after an agent failure the task is unrecoverable

team.py:122-134: the lock is created, but nowhere in the file is it deleted: verify pass (team.py:152-156) does not touch locks/, and there are no release/takeover/unclaim commands (verified by grepping the file).

Scenario 1: the process crashed between os.open (team.py:124) and save_tasks (team.py:136). The status stayed pending, the lock hangs. The next claim fails forever with "locked by another agent" (team.py:132); recovery is only deleting the file by hand.

Scenario 2: the agent died after claim. The task is in_progress and the status cannot be rolled back: claim requires pending (team.py:116-117). It can be closed through verify, but not handed to another executor.

SKILL.md:26 promises "at most 2 rounds, then the lead takes the task", but the engine does not support that: no command takes a task, so the lead is left with manually editing tasks.json and deleting locks/<id>.lock, which the documentation does not mention.

Fix: add a command `release --team --task --by <lead>` that removes the lock and sets status=pending, optionally --reassign <agent>. Additionally delete the lock on verify pass. For scenario 1, stale lock detection: on claim, when a lock exists with a ts older than N hours, print a warning with the owner name and a hint about release instead of a blunt refusal.

### R-4. Windows: os.replace over tasks.json opened by another process gives a PermissionError

team.py:50 (`tmp.replace(d / "tasks.json")`).

On Windows a rename over a file opened by another process without FILE_SHARE_DELETE (a plain read_text in load_tasks, team.py:44) ends with WinError 5. Parallel load/save by several agents is a direct way to crash a claim or a verify with an unhandled exception; an antivirus and an indexer widen the window.

Fix: a retry loop around replace (10 attempts with a 50 ms pause, then the exception with a readable message). The complete solution is the command lock from R-1: with serialized mutations there are no readers at the moment of the replace.

### R-5. events.jsonl: appends from several processes without an atomicity guarantee, and strict parsing in langfuse

team.py:56-58 (open "a" + write + flush), team.py:235 (`json.loads(l)` per line with no error handling).

On POSIX O_APPEND gives atomicity for short records; on Windows there is no such guarantee: two simultaneous appends can glue lines together or overwrite them. A broken line also appears after killing the process during a write. Then one damaged line crashes the whole export: team.py:235 throws an unhandled JSONDecodeError inside cmd_langfuse.

Fix: at team.py:235 parse with a per-line try/except json.JSONDecodeError, skipping broken lines with a warning on stderr and a counter. For log_event, write under the command lock from R-1.

### R-6. The team/task/by/to/for identifiers go into filesystem paths unvalidated

team.py:37 (root/.agent-teams/<team>), team.py:98 (init), team.py:122 (locks/<task>.lock), team.py:163, 180, 194 (inbox/<agent>.json).

`--team ../other` makes the engine read and write a directory outside .agent-teams. An agent name like `nau:bot`, or one containing `/ \ * ? " < > |`, gives an invalid file name on Windows and an unhandled OSError. init (team.py:77-81) checks only that the fields are non-empty, not the character set.

Fix: one predicate `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)` for team, task ids, by, to and for at the start of the respective commands; check the same mask in init for ids and assignees.

## MINOR

### R-7. Mailbox writes are not atomic

team.py:170, 186, 202: a plain write_text without the tmp+replace scheme (unlike save_tasks, team.py:47-50). A crash in the middle of a write leaves broken JSON, after which read and message fail at json.loads.

Fix: extract tmp+replace into a helper and apply it to every inbox write.

### R-8. The dead "verifying" status

team.py:148 accepts the status "verifying", but no command ever sets it (the single occurrence in the file is the check itself). Either remove it from the tuple, or add a transition into verifying before the verifier is started.

### R-9. The "at most 2 rounds" limit is not enforced by the engine

team.py:158: verify fail simply increments rounds; the two-round agreement lives only in SKILL.md:26. At least print a warning when rounds >= 2, and after that a hint to hand the task to the lead through the release from R-3.

### R-10. cmd_status fails as a whole on one broken mailbox

team.py:218: json.loads without a try in the loop over inbox/*.json. One damaged file (a consequence of R-7) breaks the whole status command.

Fix: a try/except per file, printing "mailbox <name>: damaged, skipped".

### R-11. The mkdir race in init

team.py:99-102: the d.exists() check and the mkdir are separated. Two parallel inits give the second one an unhandled FileExistsError instead of a clean message that the team already exists.

Fix: wrap the creation of inbox/locks in try/except FileExistsError, or use exist_ok=True after the own directory check.

### R-12. Output encoding in a pipe on Windows

team.py:203: read prints json.dumps(..., ensure_ascii=False). On a Russian Windows the stdout in a pipe (Git Bash captures the output) is encoded as cp1251: Cyrillic passes, but any character outside cp1251 in the message text (emoji, CJK) gives a UnicodeEncodeError and crashes read.

Fix: in main() call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` (Python 3.7+), or print with ensure_ascii=True.

### R-13. read marks messages as read before the actual delivery

team.py:199-203: the read mark and the write happen regardless of whether the output reached the recipient. If a subagent died right after read, the messages are already marked and will not be returned on the next poll.

Fix: a `--peek` mode (print without marking) plus an explicit `--ack <id>`, or mark as read only what the agent confirmed.

## Cross-check of "Pitfalls" (SKILL.md:72-78) against the code

- SKILL.md:74 (absolute paths in --project-root, the subagent's cwd is not assumed) matches: default="." in all subcommands (team.py:307, 313, 323, 331, 337, 342, 348), the warning is justified.
- SKILL.md:75 (the trace id is strictly 32 hex, hex32 already exists) matches: team.py:31-33, all ids in cmd_langfuse go through hex32.
- SKILL.md:76 (Langfuse v4 events_only) is an external fact, not checkable by the code, left without a remark.
- SKILL.md:77 (the mailbox without locks) is confirmed by the code, but the warning is incomplete: the second writer is the engine itself through verify-fail, see R-2. The wording should be extended.
- SKILL.md:70 (a files_write overlap is not checked by init) is confirmed: cmd_init does not validate it. A cheap check in init (a set intersection of files_write across tasks) would cut off the whole class of "two agents overwrite each other's file" errors at start time; today it is only the lead's manual discipline.
