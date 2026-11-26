# Backlog

## Done in v2.1.0a5: the product behind the spec (2026-11-26)

The last milestone of the v2.1 specification (14, 17, 18, 20, 21, 22).

- A task in plain words (14): the lead fills a brief with the owner,
  `team.py plan --brief` answers one question at a time (only four gaps
  are critical), shows the plan to confirm and builds the specification;
  `team.py init --brief` creates the team only after the owner's yes.
- Control from the board (17): with `--lead-token` the lead lends the
  board its authority; the token stays in the memory of the server. The
  owner can pause and resume, allow one more attempt, cancel a stage
  (one in work has its attempt fenced), ask for a check (a message to
  the lead), integrate, apply and raise the budget, with a confirmation
  where there are consequences. Every action is one engine operation.
- The report for a manager (18): twelve sections in one file, built
  from the board's own snapshot with the limits lifted; a link on the
  board, `GET /api/report`.
- Size and speed (21): a block is written only when its markup changed,
  a snapshot carries the newest of everything and a count of the rest,
  gzip for browsers. A hundred stages: first picture 394 to 515 ms in a
  real browser, a change on screen about 250 ms after the poll that saw
  it.
- Tests (22): the eight scenarios end to end, reading while the engine
  writes, the keyboard, every state, the command line of the brief.
- A kit for the session with real people (20): the protocol
  (`docs/usability-protocol.md`) and `examples/usability-demo/prepare.py`.
  The session itself has not been run: it needs five people.

How it was built: the first time since milestone 1 that the engine ran a
team for its own development. Two executors built the report and the
onboarding module with disjoint files through the engine; two
independent verifiers passed them on reports bound to the pinned
submissions; the engine integrated the accepted commits with a check in
the merged tree and applied them. Both executors lost their model stream
after submitting; their work was already in git.

Found on the way, none of them seen by the unit tests that existed:

- One lost executor was two problems on the board. The engine writes a
  loss twice under one event type (the timeout, then the archive of the
  workspace). The second line is a note now.
- A result that failed a real check, was reworked and was then accepted
  by the lead's decision lost its failed check. The checks that really
  happened count now, whatever accepted the result in the end.
- A real stop after two failed checks never reached the list of problems
  if the same stage had lost a specialist earlier: the rule that a lifted
  block is no problem was written too wide. The board asked for help
  about the budget alone while the attention card asked about the stage.
- A board started with the lead's authority showed no actions until the
  next change: the page learned the right only from the poll. The
  snapshot says it now.
- A stage accepted by decision was captioned "checked and accepted".
- `team.py ui` did not stop on Ctrl-C when a script had started it in
  the background (the interrupt was inherited as ignored).
- The snapshot carried the whole journal in its timeline marks and its
  incidents: 1540 KB for a hundred stages, where the page shows a
  window.

How three places of the specification were read (so the owner can say no):

- The "start screen" of 14.1 is the conversation with the lead session:
  the owner describes the task in words, the lead fills the brief, the
  engine asks the one question that matters. The board has no text field
  for a new task, and a team has to exist before a board can be opened.
- The plan of 14.2 has stages and no subtasks: a stage is the unit the
  engine knows. The plan in words that the owner confirms is made by the
  lead session from the document `plan --brief` answers (the command
  prints the document itself).
- "Approve the final result" of 17 is the apply step: once the results
  are integrated the board offers to apply them to the project, and
  asks first.
- The technical level of the integration (13) names the branch, the base
  and the resulting commit, the merged commits and the clash as the
  engine wrote it, and the files of each result. A line diff is not
  drawn on the board: `team.py diff --task` gives it.

Open after this milestone:

- The session with five people (20): the owner's.
- The visual system is recorded in `DESIGN.md` (2026-11-26, in the
  DESIGN.md format, for a polish pass with the Impeccable plugin). Known
  gaps it records, measured with the WCAG formula: the third text colour
  and the text of the "in work", "done" (light) and "problem" (dark)
  pills are under 4.5:1 on their backgrounds; the report for a manager
  has neutrals, radii and a single theme of its own. The polish pass
  decides them.
- The report is in Russian, as the board. The language decision is open.
- The page's blocks are rewritten as a whole when their markup changes;
  a stage list that changes in one row is rewritten entirely. At a
  hundred stages it costs about 200 ms, well inside the target; a finer
  update would be work for a size nobody has asked for.

## Done in v2.1.0a4: what the numbers mean (2026-11-25)

Budget control (v2.1 spec 11.2) in the engine: `budget_usd`,
`budget_per_task_usd` and `budget_warn_ratio` in the config, the
`team.py budget` command for the lead, a gate in `claim` that refuses
new work once the budget is spent and stops nothing running. Four cards
on the board (v2.1 spec 10 to 13): the quality of the results, the
spend against the budget, the problems and how each ended, the
integration; and the comparison of teams for a declared benchmark
(v2.1 spec 19), with a sentence about speed or money only from two
finished teams of one set.

The first cut was checked against the text of the specification before
it was called done, and five gaps were found and closed:

- The quality card had figures but neither the distribution of the
  results nor the checks in their order, and a found defect was named
  by the identifier of its task instead of its title.
- Every sum per stage and per specialist was shown as the whole truth
  while some of its records had no price; a stage with no priced record
  was not shown at all. Now each sum says what it leaves out, and such
  a stage is "not defined".
- The technical level had no tokens and no price per model (v2.1 spec
  11.3).
- A spent budget was on the attention card and on the money card, but
  not among the problems of the recovery centre: the engine refuses the
  start and writes no event.
- The comparison measured time, money and first-pass results only;
  attempts, problems and clashes between results were not counted.

Found on the way:

- On a phone the sticky header grew to four rows (an oval that hid a
  quarter of the screen) as soon as the goal needed two lines. The
  start and the elapsed time are on the "now" card anyway; the header
  is two short rows now. The screenshots of the earlier milestone had
  been taken and not looked at closely enough.
- Two tables of eight and nine columns hid their last columns behind
  the edge of the card. Both are a block per row now (a model, a team):
  every measure is visible at any width.
- A pinned hand-over that changed no file was shown with the files the
  task declared, as if the result had them. It is an empty result: the
  task's sheet says so, and the quality card names it (the item from
  v2.1.0a3 below).
- Figures took no notice of their number ("1 проверок", "Цена 1 запись
  неизвестна"). The words take their endings from the figure now.
- A money limit compared at the precision of floating point: a budget
  that was exactly spent was not enforced. Both sides are compared at
  the precision of the price table.
- In shared mode (no git) a PASS given with an override reason was not
  recorded as a decision of the lead: the engine marked it only when
  there was a snapshot to bind a report to, and the board counted it as
  a passed check. A reason given is the lead's decision in any mode now.
- The checker's card counted an attempt recorded behind a decision of
  the lead among its checks ("1 of 1" for a team whose only acceptance
  was a decision). Its checks are the ones it really made.
- `team.py ui` did not stop on Ctrl-C when a script had started it in
  the background: a non-interactive shell leaves SIGINT ignored in such
  a child, Python keeps what it inherits, and the process sat in its
  select forever. The command takes the signal back now. Found because
  two test suites started as background jobs failed four tests each
  that pass in the foreground; the first guess (load) was wrong, a
  stack dump of the stuck process showed the select loop.

## Done in v2.1.0a2: the board for a non-technical owner (2026-11-25)

`team.py ui` (spec 16): a read-only local page with the plan, what is
happening, what needs the owner's decision and why, and the activity
feed, in plain words; two levels from one snapshot. Tested at four
layers: the words, the read-only snapshot over a git-backed team, the
HTTP server, and the page script under Node.

Defects found and fixed on the way:

- A flash model was priced as its parent. The price table was searched
  by substring in dictionary order, and "glm-5.3" comes before
  "glm-5.3-flash": on input a flash model cost 6 times too much. The
  longest matching name wins now.
- No cost was ever stored. `cost_usd` stayed NULL, every reader
  recomputed from the current price table, and the manual `tokens`
  command bypassed the only writer there was. The cost is stored when
  the record is written and reported from there by `cost`, `metrics`,
  the report and the board.
- The CLI dropped nested data in text mode: `diff`, `cost`, `graph`
  and `workspace` printed `OK task=a` and nothing else. A nested result
  is printed whole now, and every command accepts `--json`.
- A trap worth knowing: a `--json` flag shared by all subcommands is
  one argparse action object, and `set_defaults(json=True)` on two
  commands switched JSON on for all of them. Caught by a test before
  the release; text stays the default everywhere.
- SKILL.md told the lead to PASS without the verifier's report, which
  a git project rejects (the first finding of the docs audit).
- `main` was red on Windows since v2.1.0a1 and nobody looked: the
  rename tests built `double "quote".txt`, a name Windows refuses, and
  the whole file errored at setup. Such names are skipped there now.
  Found only because the next pull request read its own CI results.
- The board's server resolved its own address into a domain name at
  start. A network lookup the board does not need; on the CI runners of
  macOS it took longer than the test's patience. Removed: the board
  answers by address and works offline.

Found while checking the first cut of this milestone against the text
of the spec (it was not complete, and said so only after that check):

- The list of specialists was not on the board at all, though the spec
  puts "understandable specialist cards" in this milestone. Added, with
  the checker as a specialist and a sheet per specialist.
- The header had no project name, goal or start time. The spec format
  takes optional `title`, `goal` and a task `description` now (1.1).
- A stage said neither what comes next nor what result is expected.
  Added, with the three checkpoints the engine records.
- Every poll took the whole snapshot. Now a light "has anything
  changed?" comes first (spec 16.3).
- An executor with tasks of different lenses was named by its first
  task on every line of the feed, while the task card named it by its
  own role. On a task's event the task's role wins.

## Done in v2.1.0a3: seeing the work (2026-11-25)

The plan as a left-to-right graph (zoom, drag, search, filters, the
position kept across refreshes), a timeline drawn only from the
journal, the files of a result opened as they were accepted, and an
activity feed with filters by type, specialist, stage and time, a
pause, search and reading further back.

Found on the way:

- A result accepted with nothing in it is shown as such ("handed over,
  no files"), not as a missing result: the board of the demo team
  revealed a task accepted by decision whose executor had produced no
  file at all. Worth a line of its own in the quality dashboard (M4).
- Two executors with one role are told apart by a number on their cards
  and on every line of the feed.
- On Python 3.10 the status "413 Content Too Large" carries another
  name in the standard library; the board's server used the 3.11 name
  and crashed there. Caught by running the new suites on a real 3.10.
- A lead token could start with a dash: `secrets.token_urlsafe` draws
  its first character from an alphabet where that happens once in 64.
  On a command line `--lead-token -abc` reads as a missing value, and
  the engine refused its own token with `expected one argument`. So one
  team in 64 could not be led by the playbook, and in CI it looked like
  a test failing at random (it did, on `main`, right after a release
  that had passed 17 of 17 on its branch). New tokens start with a
  letter or a digit, and the command line now carries a dash-leading
  value to the engine for the teams that already exist.

## Open, found while building the board (2026-11-25)

- The snapshot's `metrics` are still empty: `collect_metrics` needs a
  store with a live connection, and the adapter's failure was swallowed
  into `{}`. The board does not need it any more (its numbers are built
  on the snapshot's own data, `analytics.py`); the field should either
  go or be filled from the same functions, so that two readers never
  disagree.
- Money is shown in USD with the currency named beside the figures. A
  currency of the reader's choice (v2.1 spec 11.1) needs an exchange
  rate the engine cannot know offline: a rate in the config, set by the
  owner, would do; not built.
- Two problem types of v2.1 spec 12.1 have no event of their own in
  this engine: missing data (shown where the engine records it, as a
  stage waiting for another one) and a clash between results (shown as
  a problem of the integration). If the engine learns to record them,
  the recovery centre should tell them apart.
- The behaviour at the budget limit is the one the specification
  describes and is not a setting (v2.1 spec 11.2 lists it among the
  settings). A second behaviour would need a reason to exist.
- The comparison of 1, 2, 3 and 5 executors (v2.1 spec 19) has its
  machinery and no campaign yet: it needs real runs of one declared
  set. Until then the card says there is nothing to compare.
- The technical block under each card is a plain dump of the fields.
  Readable enough for a technical reader; a table would be kinder.
- The texts of the board are Russian, in a repository whose rule is
  technical English with READMEs in five languages. For the alpha that
  is the language of its first users; before a stable 2.1.0 the strings
  should move out of the code into a dictionary with English as the
  base. The owner decides.
- The board has no authentication and binds to loopback by design. An
  owner who is not at the machine needs a proxy with authentication in
  front of it; not built.
- What a line of the plan graph means ("the result of stage X is
  needed for stage Y") is its tooltip today. A reader on a phone has
  no tooltip; the list below the graph says the same in its "после"
  line, and a visible label on the line would be better.
- In shared mode (no git) the engine keeps no diff of a result, so the
  board shows the files the task declared. A result that wrote another
  file is not seen there.
- Two quirks of the journal that every human-facing reader has to
  know. A schema change could remove the need:
  - a manual acceptance is two `verification_passed` events, the
    decision (with `override`) and then the transition (without);
  - recovery of a lost executor writes `task_blocked` and releases the
    task in the same step.

## Engine defects found while running a real team (2026-11-25)

Found by driving the engine as a lead during the v2.1 Milestone 1 run.
Each one cost real time or produced a wrong verdict.

- A weak machine criterion can accept an EMPTY snapshot. The criterion
  `pytest tests/unit/test_storage_migration.py` existed before the task,
  so it passed on a submission pinned to the run base with no work in it;
  the lead override then completed the task. A criterion must prove the
  new artefact exists in the snapshot (`test -f <new file> && ...`)
  before running tests. The engine cannot know what "new" means, so this
  is a documentation and template rule, plus a possible warning when
  `changed_files` is empty on a submit that is not a shared-mode task.
- Task branches are named after the team, not the run
  (`agent-teams/<team>/<task>/a<n>`). Rebuilding a team with the same
  name reuses the old branches, and a new attempt silently starts from
  the previous team's work. Branch names should include the run id.
- A lead cannot submit on behalf of an executor whose execution token was
  lost (the agent crashed or hit the provider rate limit). The only path
  is recover, a new claim and carrying the commit into the new worktree
  by hand. A lead-side `submit --for <task>` with an audited reason would
  make this a first-class operation.
- There is no way to abort a team. `cancel` is allowed only from
  `pending`, so tasks that are in progress or awaiting verification when
  the lead decides to stop cannot be closed through the engine.
- Machine criteria run with the system `python3` from PATH. On a machine
  where pytest is not installed there (Homebrew Python is PEP 668
  managed), a `python -m pytest` criterion fails with exit 1, and the
  lead override does not bypass it. Criteria should name their
  interpreter or the engine should expose the interpreter it runs under.

## Done in the production hardening (2026-11-24, v1.1.0 - v2.0.0)

- v1.1.0: immutable execution (clean submit, pinned snapshots,
  verification against the snapshot, accepted SHA, DAG-ordered SHA
  integration, separate apply, single run base)
- v1.2.0: recovery and fencing (dual timeouts, per-attempt workspaces
  and branches, quarantine, dead old tokens, idempotent recovery)
- v1.3.0: enforced verification reports (schema, submission binding,
  coverage and evidence rules, audited lead overrides)
- v1.4.0: observability counters, source-quality labels, report
  sections for submissions, recovery and integration
- v2.0.0: SPEC.md, explicit versioning, docs and README x5 updates,
  schema and manifest CI checks

The engine received 30 findings in its first run (examples/demo-run/findings-*.md).

## Done in v0.2.1 (2026-11-23)

- English translation of the whole repository and of all engine output. The output is pure ASCII now, which also closes the Windows pipe encoding issue (findings-robust R-11)
- README in five languages: en, ru, es, fr, zh
- Localized marketplace and plugin manifest metadata (ru, es, fr, zh-CN)
- Repository topics on GitHub

## Done in v0.2.0 (2026-10-30)

- Read-modify-write races on tasks.json and the mailbox: one state lock `.state.lock` (O_EXCL, retries, reclaim of a lock that is stale for more than 2 h) on all mutations + atomic writes through a unique tmp file (pid + uuid): findings-code M1/M2, findings-robust R-1/R-2/R-7
- Path traversal: team, agent and task id names are validated with the regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`: findings-code M4, findings-robust R-6
- Orphaned states: the `release` subcommand returns a task to pending: findings-code M3, findings-robust R-3
- Windows PermissionError on replace: a retry with a growing delay: findings-robust R-4
- Corrupt events.jsonl lines do not crash the langfuse upload: findings-robust R-5
- Round limit enforcement: after failed verification rounds a task goes to `blocked` and a ROUND LIMIT feedback goes to the mailbox: findings-robust R-9
- Economics: `tokens --task --add N`, counters in tasks.json and status, total tokens in the trace metadata and in the span

## Minor (open)

- The dead "verifying" status: verify accepts it, but nothing assigns it (findings-code m1). It can be removed
- Round limit is off by one: with MAX_ROUNDS = 2 a task is blocked on the third failed verification, because the counter is compared before it is incremented. Fix together with configurable limits in v0.3.0

## Distribution

- `zcode plugins validate` in CI
