# Audit report: races and atomicity of v0.2.0

Auditor: aud-races (concurrency lens). Object: `plugins/agent-teams/skills/agent-teams/scripts/team.py`, 483 lines. The audit is static, from the code; no live concurrency test of two claims was run (per the task condition).

## Checklist

| # | Item | Verdict | Where |
|---|---|---|---|
| 1 | One state lock on all mutations of tasks.json and the mailbox (claim/verify/message/read/release) | OK | team.py:83, 178, 200, 244, 279, 294 |
| 2 | A unique tmp (pid+uuid) in atomic writes | OK | team.py:70 |
| 3 | An os.replace retry on PermissionError | OK | team.py:58-66 |
| 4 | Reclaiming a stale lock | OK | team.py:94-99 |
| 5 | No live concurrency test, static audit only | Honored | - |

### 1. One lock: OK

`StateLock` always creates the lock at the same path `locks/.state.lock` (team.py:83), so all commands serialize through one file. The read -> change -> write cycle sits entirely inside the lock for all five checklist commands:

- claim: entry at team.py:178, read at 179, mutation and write at 187-189, log_event at 190;
- verify: entry at team.py:200, read at 201, the tasks.json writes at 209, 216, 227, the mailbox writes (save_mailbox) at 218-221 and 230-233, all inside the lock;
- release: entry at team.py:244, read at 245, mutation and write at 249-250;
- message: entry at team.py:279, save_mailbox at 280-282 inside the lock;
- read: entry at team.py:294, the mailbox read at 295, the "read" marking at 297-298, the write at 299.

A caveat: `tokens --add` also mutates tasks.json, but its read sits outside the lock, see residual risks, item 1.

### 2. Unique tmp: OK

team.py:70: `f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"`. The process id plus 8 hex characters of uuid4: a name collision between two writes is practically excluded, even within one process.

### 3. The os.replace retry: OK

`replace_retry` (team.py:58-66): up to 5 attempts with a growing wait of 0.2-1.0 s, then a final call that lets the exception propagate. It is called from `write_atomic` (team.py:72), and all state writes go through it: tasks.json at team.py:166, 189, 209, 216, 227, 250, 264; the mailbox at team.py:133, 299. There are no unprotected state writes bypassing this mechanism.

### 4. Reclaiming a stale lock: OK

team.py:94-99: on FileExistsError the age is computed from the lock file's mtime; if it is older than `LOCK_STALE_S = 7200.0` (team.py:32, 2 hours), the lock is deleted and the loop repeats: the new acquisition goes through `O_CREAT | O_EXCL` (team.py:90), so of several waiters exactly one gets the lock. While the lock is fresh, the waiter sleeps 0.2 s up to the `LOCK_TIMEOUT_S = 10.0` deadline (team.py:87, 102-104).

## Residual risks: what the lock does NOT protect

1. **tokens: the read and increment outside the lock.** team.py:258 reads tasks.json without the lock, team.py:261-262 changes `tokens` in memory, only the write is under the lock (263-264). Two concurrent `tokens --add` commands read the same base value, and the last write erases the first addition (a lost update). Also, the `log_event` call in tokens happens outside the lock (team.py:265): the only command with this deviation from the pattern of the others. Fix: move the read inside `with StateLock`.

2. **Simultaneous init.** team.py:162 checks `d.exists()`, team.py:164-165 does `mkdir(parents=True)` without handling `exist_ok=False`. Two parallel inits of one team both pass the check before the directory exists; the second fails with an unhandled `FileExistsError` (a traceback instead of a readable message), and with a different interleaving its `write_atomic` could overwrite the other's tasks.json. StateLock does not apply here: there is no directory and no lock yet. Mitigation: `mkdir(exist_ok=True)` with a repeated check and a refusal before the first write.

3. **status reads without the lock.** team.py:308 (tasks.json) and 315-317 (all mailbox files). No data corruption: the atomic file replacement gives the reader either the old or the new content, but status can show a stale state in the middle of someone else's transition. On Windows an open reader forces the writer into `replace_retry` retries (up to about 3 s of delay per write).

4. **langfuse reads without the lock.** team.py:359 (tasks.json), 361 (events.jsonl). The export is built from a snapshot "as of the read": spans can get an inconsistent combination of claimed_at/completed_at (for example the claimed_at of the previous round after a release). No mutations, only a distorted reporting picture.

5. **The events.jsonl log.** `log_event` (team.py:114-118) is a plain append without a lock when called from tokens (team.py:265); in claim/verify/release/message the log is written inside the lock and serialized. Parallel appends can in theory interleave lines; the Langfuse export survives broken lines (team.py:364-367), but the event is then lost from the report.

6. **Stale lock reclaiming can break mutual exclusion.** team.py:97-99 removes the lock of an owner stuck for more than 2 hours; team.py:106-111 in `__exit__` removes the file by path without checking that it is still "its own" lock. If the original owner is alive and finishes after its lock was reclaimed and taken over, it will delete someone else's fresh lock, and two processes end up in the critical section. Practically unreachable with millisecond critical sections and a 2-hour threshold; the risk appears only on a SIGKILL inside the section or a machine going to sleep with an open lock.

7. **Write reliability is not the same as atomicity.** `write_atomic` (team.py:70-72) does no flush+fsync of the temporary file before the replace: after a power or OS failure the resulting file can turn out empty, or the replacement can be lost. If the final `os.replace` in `replace_retry` does throw, the orphan temporary file stays on disk: no cleanup is provided on the exceptional path.

8. **The lock wait boundary is a sys.exit.** team.py:102-103: waiting longer than 10 s ends the process with an error. It does not break safety (the task stays available), but under dense contention an agent dies instead of waiting: a liveness risk, not an integrity one.

9. **Outside the concurrency lens.** `cmd_read` does not validate `--for` (team.py:290: the path `inbox/{args.for_}.json` without `check_name`): a possible path traversal when reading a mailbox. It belongs to the aud-paths checklist, recorded here only.

## Conclusion

All four checkable checklist items are OK. The code matches the concurrency model stated in the docstring (team.py:11-13) for the five checklist commands. The main real hole is `tokens --add` with its read outside the lock (risk 1): there is a tasks.json mutation, but no full cycle under the lock. The rest is unlocked reads (status, langfuse), the init race and theoretical scenarios after a stale lock reclaim.
