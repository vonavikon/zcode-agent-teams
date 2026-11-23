# Audit report: name validation, release, round limit (v0.2.0)

Object: `plugins/agent-teams/skills/agent-teams/scripts/team.py` (483 lines, the revision at audit time).
Lens: safety. Auditor: aud-paths. Date: 2026-10-30.

All `team.py:N` references point to the current file in the repository.

## Verdict summary

| # | Checklist item | Verdict | Where |
|---|---|---|---|
| 1 | NAME_RE validation of names on input (team / --by / --to / --for / id / assignee) | NOK (2 gaps out of 8 entry points) | details below |
| 2 | release returns a task to pending | OK | team.py:249 |
| 3 | verify fail with rounds>=2 -> blocked + ROUND LIMIT in the mailbox | OK | team.py:213-224 |
| 4 | claim updates assignee to the actual executor | OK | team.py:187-188 |

## 1. NAME_RE name validation

The pattern and the check: `NAME_RE` at team.py:33, `check_name()` at team.py:45-47. The pattern `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` cuts off slashes, backslashes, leading dots and the empty string: enough for building paths.

Every place where a name goes into a path or into the state was examined (the file has exactly three families of path building from user input: the team directory, the mailbox file, the tmp name):

### team (--team): OK

- init: `check_name("team", args.team)` at team.py:140, the path `root/.agent-teams/<team>` is built at team.py:161.
- All other commands go through `team_dir()`, which validates the name before building the path: team.py:51. Call sites: claim team.py:177, verify team.py:199, release team.py:244, tokens team.py:258, message team.py:278, read team.py:289, status team.py:307, langfuse team.py:353.

### --by: NOK in tokens, OK everywhere else

- claim: team.py:176, OK.
- verify: team.py:198, OK.
- release: team.py:243, OK.
- message: team.py:277, OK.
- tokens: no check anywhere in `cmd_tokens` (team.py:255-269). Mitigating: `--by` there never reaches a path, only the owner string of the lock (team.py:263) and the actor field of the log (team.py:265-266), both JSON content, escaped by `json.dump`. But it is the only mutating command that lets a name past `check_name`, and the consistency of "validate every name on input" breaks here. The verdict follows the letter of the checklist.

### --to (message): OK

`check_name("agent", args.to)` at team.py:276, before `save_mailbox` -> `inbox/<to>.json` (team.py:130, the call at team.py:280).

### --for (read): NOK, the main finding of the audit

`cmd_read` (team.py:287-300) builds the path `inbox/<for_>.json` at team.py:290 with no validation at all:

```python
def cmd_read(args) -> None:
    root = Path(args.project_root).resolve()
    d = team_dir(root, args.team)
    mb = d / "inbox" / f"{args.for_}.json"   # team.py:290: args.for_ is unchecked
```

Consequences: `--for ../../somewhere/name` reads an arbitrary `name.json` relative to the team directory (within the process rights), and then team.py:294-299 writes the same file back through `write_atomic` (marking `read: True`). So it is not only reading a foreign file, but also writing outside `inbox/`. The fix is one line: `check_name("agent", args.for_)` after team.py:289.

### Task ids at init: OK

`check_name("task id", t["id"])` at team.py:148, plus the duplicate guard at team.py:150-151. In the other commands the `--task` argument never reaches a path, only the comparison in `find_task` (team.py:122-126); a wrong value ends the process with no side effects.

### assignee at init: OK

`check_name("executor", t["assignee"])` at team.py:149.

Item 1 totals: 6 of 8 entry points are closed, 2 are open (`--for` in read, reaching a path; `--by` in tokens, not reaching a path).

## 2. release returns a task to pending: OK

`cmd_release` (team.py:240-252): release rejects a completed task (team.py:247-248), otherwise `t.update(status="pending", claimed_at=None, last_event_ts=now_iso())` at team.py:249. The write is under the shared lock (team.py:245), atomic (team.py:250), with a `task_released` event recording the number of rounds at team.py:251.

From blocked (after ROUND LIMIT) release works too: the guard passes every status except completed. The "blocked -> release -> pending -> claim" cycle is closed.

A note (not affecting the verdict): release does not reset `assignee`, so until the next claim `status` shows the previous executor. It does not affect feedback routing: verify requires in_progress (team.py:203), and claim overwrites assignee (team.py:188).

## 3. verify fail with rounds>=2 -> blocked + ROUND LIMIT: OK

`MAX_ROUNDS = 2` at team.py:30. The fail branch of `cmd_verify`:

- the threshold: `if t["rounds"] >= MAX_ROUNDS:` at team.py:213;
- the move to blocked: team.py:215;
- the `round_limit` event: team.py:217;
- the message to the executor with the text "ROUND LIMIT on task ...: the limit of 2 failed verification rounds is reached, the task goes to the lead (release or handle manually)": team.py:218-221 (the text at team.py:220);
- the `return` at team.py:224 ends the branch before the rounds increment: one message, the counter does not grow.

The threshold semantics: the first and second fail return feedback to the executor (rounds -> 1, then 2, team.py:225-235), the third fail with rounds==2 goes to blocked. The executor gets exactly two rounds of fixes, which agrees with the "at most 2 rounds" clause of the team rules.

The addressee of the message is `t["assignee"]`, and claim updates it to the actual executor (see item 4), so the ROUND LIMIT goes to whoever really did the work, not to the nominal addressee from the spec file.

A repeated verify on a blocked task is impossible: the guard at team.py:203-204 passes only in_progress/verifying.

## 4. claim updates assignee: OK

team.py:187-188:

```python
t.update(status="in_progress", claimed_at=now_iso(), last_event_ts=now_iso(),
         assignee=args.by)  # owner = actual executor: acceptance feedback goes to them
```

`args.by` is validated (team.py:176), the update runs under the lock (team.py:178), the write is atomic (team.py:189). All later messages (verify fail at team.py:230, round limit at team.py:218) are addressed to the actual executor.

## Residual risks

1. **`--for` in read is not validated (commented in item 1, repeated here as a risk).** The only name that reaches a path without NAME_RE. Exploiting it requires running the command with a `--for` containing `..`, that is, the attacker must already be allowed to run team.py. The threat is not remote, but the isolation of the agents' mailboxes from each other (reading a foreign box, replacing its content through the write) breaks. Fix: one line of check_name.

2. **Validation happens only on input, the state is trusted.** `save_mailbox` (team.py:129-133) builds the path from `t["assignee"]` without a repeated check. If tasks.json is edited by hand (bypassing init/claim), a verify fail message goes along a path with `..`. An accepted risk (the `.agent-teams/` trusted zone), but worth remembering: NAME_RE protects the ingress, not the storage.

3. **`--by` in tokens is not validated.** It never reaches a path (the lock owner and the event actor are JSON content), but it is a hole in the rule "every name through check_name" and an easy channel for garbage in events.jsonl.

4. **NAME_RE does not filter reserved Windows names.** `CON`, `NUL`, `AUX`, `COM1`-`COM9`, `LPT1`-`LPT9` pass the pattern; `inbox/CON.json` on Windows resolves to a device, not a file (the behavior depends on the API). Not a traversal, but a source of hard-to-debug failures on a platform the project targets. The same for `--team CON`.

5. **`deps` at init do not go through check_name** (team.py:157). They never reach a path (only the comparison in find_task, team.py:184), at worst a "task not found" refusal. Consistency suffers, security does not.

6. **`files_read`/`files_write` are a declaration without enforcement.** The engine nowhere restricts an agent's writes to the `files_write` list; that contract rests only on process discipline. For a safety audit this means: write isolation between agents is not mechanical.

7. **A side observation outside the lens (for aud-races):** `cmd_tokens` reads and mutates tasks.json before taking the lock (the read at team.py:258-259, the mutation at 261-262, the lock only on the write at team.py:263-264): a classic lost update window. It belongs to the other checklist, recorded here only.

8. **`--tasks` in init is an arbitrary read path by design** (team.py:141). Expected behavior, not a risk; noted for completeness.

## Conclusion

Of the four checklist items, three close fully (release, round limit, assignee on claim); item 1 has two gaps: a critical one (`--for` in read, reaching a path, read plus write outside inbox) and a non-critical one (`--by` in tokens, never reaching a path). Both are fixable with one line of `check_name` each.
