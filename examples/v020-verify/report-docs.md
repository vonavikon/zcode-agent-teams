# Audit of the v0.2.0 documentation and manifest consistency (audit-docs)

Team v020-verify, executor aud-docs, 2026-10-30. Lens: docs.

The reference is the current `plugins/agent-teams/skills/agent-teams/scripts/team.py` (483 lines, v0.2.0). Checked against it: `plugins/agent-teams/skills/agent-teams/SKILL.md`, `README.md`, `BACKLOG.md`, `plugins/agent-teams/.zcode-plugin/plugin.json`, `marketplace.json`.

## Item 1. Version 0.2.0 in plugin.json and marketplace.json: OK

- plugin.json:3: `"version": "0.2.0"`
- marketplace.json:7: `"version": "0.2.0"`

The indirect mentions agree: team.py:11 ("Concurrency v0.2.0"), README:7 ("(v0.2.0)"), BACKLOG:5 ("Done in v0.2.0").

## Item 2. SKILL.md commands against team.py: OK

All nine commands exist and the flags match. `--team` and `--project-root` are added to every subcommand by a shared helper (team.py:429-433), which matches the SKILL.md:59 statement "Every command accepts --project-root".

| Command | SKILL.md | Parser in team.py | Flags beyond --team/--project-root |
|---|---|---|---|
| init | :50 | 435-437 | --tasks (436) |
| claim | :51 | 439-442 | --task (440), --by (441) |
| verify | :52 | 444-449 | --task (445), --verdict pass\|fail (446), --feedback (447), --by default verifier (448) |
| release | :53 | 451-454 | --task (452), --by default lead (453) |
| tokens | :54 | 456-460 | --task (457), --add int (458), --by (459) |
| message | :55 | 462-466 | --to (463), --by (464), --text (465) |
| read | :55 | 468-470 | --for -> for_ (469) |
| status | :56 | 472 | - |
| langfuse | :57 | 474-476 | --name (475) |

The semantics in the SKILL.md descriptions match the code: verify fail requires --feedback (team.py:205-206), fail puts the feedback into the executor's mailbox (230-234), after 2 failed rounds the task goes to blocked with ROUND LIMIT (213-224); release returns it to pending (249); tokens is visible in status (311-312) and in langfuse (385, 401).

## Item 3. README "What is inside" (lines 5-14): OK

All four mechanics from the checklist are confirmed by the code:

- State lock: README:7 ("Every mutation runs under a single state lock and is written atomically") <-> team.py:79-111 (StateLock); used in claim (178), verify (200), release (244), tokens (263), message (279), read (294); the atomic write is write_atomic (69-72).
- Blocked: README:10 ("a task that keeps failing verification goes to blocked") <-> team.py:30 (MAX_ROUNDS = 2), 213-224 (round_limit -> blocked + a ROUND LIMIT message to the executor's mailbox).
- Release: README:10 ("comes back to the lead through release") <-> team.py:240-252 (the return to pending, forbidden only for completed, 247-248).
- Tokens: README:12 ("cost is visible in the UI") <-> team.py:255-269 (cmd_tokens), 385 (tokens_total in the trace metadata), 401 (tokens in the span). README does not name the tokens command explicitly, no contradiction.

The rest of the block also matches: mailbox README:8 <-> team.py:129-133; locks/O_EXCL/stale reclaim README:9 <-> team.py:83, 90, 32 (LOCK_STALE_S = 7200), 96-99; events.jsonl README:9 <-> log_event (114-119): every status change is logged (task_claimed 190, verify_pass 210, verify_fail 228, round_limit 217, task_released 251); the exit-2 gate README:11 <-> team.py:205-206, 230-234; langfuse README:12 <-> team.py:374-419.

## Item 4. BACKLOG "Done in v0.2.0" (lines 7-13): OK

All seven items are confirmed by the code, there are no promises beyond it:

| BACKLOG | Promise | Confirmation in team.py |
|---|---|---|
| :7 | one .state.lock (O_EXCL, retries, stale after >2 h) on all mutations, tmp pid+uuid | 79-111 (StateLock), 32 (LOCK_STALE_S), 69-72 (write_atomic), 58-66 (replace_retry) |
| :8 | name validation with the regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` | 33 (NAME_RE), 45-47 (check_name); call sites: 51, 140, 149, 176, 198, 242, 276-277 |
| :9 | release returns a task to pending | 249 |
| :10 | PermissionError retry on replace | 58-66 (sleep 0.2*(i+1), 5 attempts) |
| :11 | broken events.jsonl lines do not crash langfuse | 361-369 (JSONDecodeError -> the bad counter) |
| :12 | round limit: blocked + ROUND LIMIT in the mailbox | 213-224 |
| :13 | tokens --add, counters in tasks.json/status, totals in the trace and the span | 456-460, 159, 261, 311-312, 385, 401 |

A caveat on line 7, see discrepancy 5 below: the read in cmd_tokens happens outside the lock.

## Discrepancies beyond the checklist

1. **SKILL.md:81: an outdated mailbox description (a direct contradiction of the code).** "The mailbox is read-modify-write without locks: do not let two agents write into the same box at the same time." In v0.2.0 all mailbox mutations run under StateLock: message (team.py:279), read (294), the writes from verify (218, 230, inside the `with StateLock` block from line 200). BACKLOG:7 and README:7 describe the mechanics correctly. This leftover v0.1 text is the only place where the documentation claims something the code no longer does.

2. **SKILL.md:59: "the engine rejects the rest" of the names: more is promised than is checked.** (a) The regex requires an alphanumeric first character, a leading "."/"-"/"_" does not pass (team.py:33), and SKILL.md is silent about that. (b) `read --for` and `tokens --by` are not validated at all: cmd_read builds the path `d / "inbox" / f"{args.for_}.json"` without check_name (team.py:287-290), cmd_tokens does not check args.by (255-263). For those two flags the SKILL.md:59 statement does not hold. Cross-referenced with the aud-paths audit.

3. **SKILL.md:14: the mapping table describes the v0.1 mechanics.** The row "shared task list + file locking on claim -> `.agent-teams/<team>/tasks.json` + lock files `locks/` (O_EXCL)". In v0.2.0 locks/ holds one state lock .state.lock (team.py:83) that serializes all mutations, not per-claim locks. README:9 is worded correctly. A wording inaccuracy, not a functional one.

4. **SKILL.md:26 and :52: the reference "see the repository BACKLOG" leads nowhere.** There really are no reset/reassign commands in the engine (the team.py parser, 425-476, has the nine subcommands listed in item 2). But BACKLOG contains no reset/reassign item, neither in "Minor (open)" nor in "Distribution".

5. **team.py:258: the tasks.json read in cmd_tokens happens outside the lock.** Only the write is under the lock (263-264): between the read (258) and the write a concurrent verify can lose an update (a lost update). Formally README:7 "every mutation runs under a single lock" is true about writes, but the read-modify-write protection in tokens is incomplete. Cross-referenced with the aud-races audit (the residual risks of the lock).

6. **Side checks without discrepancies.** references/prompt-teammate.md and references/prompt-verifier.md exist (SKILL.md:23-24 refers to them); examples/tasks-review.json and examples/demo-run/ exist (README:56-57); the plugin descriptions in plugin.json:4 and marketplace.json:8 match verbatim; README:18 (the installation path through zcode-agent-teams -> agent-teams) matches the marketplace.json:2-13 structure.

## Verdict

Checklist: 4 of 4 OK. Beyond the checklist: 5 findings, one hard one (SKILL.md:81 contradicts the v0.2.0 code), the rest are wording inaccuracies and cross-references to the neighboring audits (aud-paths, aud-races).
