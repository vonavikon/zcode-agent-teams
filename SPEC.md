# agent-teams engine specification

This document is the single source of truth for the engine's behavior.
README files describe the product, SKILL.md the lead's playbook, docs/
the mechanism details; when they disagree with this file, this file wins.

## Versioning

| Artifact | Where | Current |
|---|---|---|
| Plugin | plugin.json, marketplace.json | 2.1.0a4 |
| Engine | agent_teams/version.py ENGINE_VERSION | 2.1.0a4 |
| Task specification format | agent_teams/version.py SPEC_VERSION | 1.1 |
| SQLite schema | storage.SCHEMA_VERSION (PRAGMA user_version) | 4 |
| Verification report | verification.REPORT_SCHEMA_VERSION | 1.0 |

The versions evolve independently; every change to behavior bumps the
engine version, every storage change adds a migration.

## 1. Purpose and scope

The engine lets one lead session run a team of subagents over a shared
code base with an acceptance loop enforced technically: the artifact
that passed verification is byte-for-byte the artifact integrated into
the project. Out of scope: sandboxing a hostile executor with arbitrary
machine access (see Security model).

## 2. System architecture

Package `agent_teams/`: cli (entry team.py), engine (operations),
statemachine + storage (state), locking (OS file lock), validation
(spec checks), workspaces (git worktrees, snapshots), quality (machine
gate, integration merge), verification (report validation),
observability (usage, cost, metrics), report (HTML), langfuse (export),
viewmodel (the reader's words), analytics (what the numbers mean:
quality, budget, problems, integration, the comparison of teams),
dashboard_data + dashboard_server and dashboard/ (the read-only board,
section 16). The v0.2 single-file
engine remains as team_v02.py for legacy teams.

Team directory `.agent-teams/<team>/`: state.db (SQLite, WAL), locks/
(OS lock), backups/, tasks/<task>/attempts/<n>/workspace (per-attempt
worktrees), tasks/<task>/quarantine/ (fenced attempts),
integration/workspace, verify-ws (ephemeral), and v0.2 mirrors
(tasks.json, inbox/, events.jsonl).

## 3. Roles and permissions

Lead (the main session): holds the lead token printed once by init and
stored hashed; applies verdicts, releases, cancels, recovers,
integrates, applies, overrides. Executors (subagents): claim with a
per-attempt execution token printed once, heartbeat, submit. Verifiers
(read-only subagents): check and return structured reports; they hold
no tokens and cannot change state. A free-text `--by` value is an actor
record and grants nothing.

## 4. Task specification

JSON with optional title (the name of the team for people), goal (what
it is for), config and agents, and a non-empty tasks array
(schemas/tasks.schema.json). The config may carry a budget (section 17)
and declare a benchmark (section 18). A task may carry a description (what it
is about). These three are words for people: optional, shown by the
board as they are (specification format 1.1; older specifications stay
valid). Validated before creation: unique ids,
name patterns, acceptance criteria (structured with types
test/code_review/file_check/security_check/documentation/manual, or
legacy strings), dependency DAG (unknown deps, cycles rejected), path
rules (no absolute/traversal/reserved paths, symlink escape detection),
write overlaps (error unless ordered by deps or explicitly allowed with
a reason).

## 5. State machine

Task statuses: pending, in_progress, awaiting_verification, completed,
blocked, cancelled; completed and cancelled are terminal. Every
transition checks the edge, the initiator role, guards on the previous
status, and appends a hash-chained event. Failed verdicts count
immediately: the Nth failed round blocks the task
(max_verification_failures, default 2). Recovery events (timeout,
quarantine) are events and attempt records, not task statuses.

Run statuses: active, integration_running, integration_failed,
apply_pending, finished. A run never claims "finished" until apply (or
a shared-mode integrate) actually landed.

## 6. Execution lifecycle

init validates, records the run base commit (HEAD of the git project),
creates the lead token. claim checks roles and dependencies, prints the
execution token, creates the per-attempt workspace and branch from the
task's last verified checkpoint or the run base, sets the lease.
heartbeat refreshes activity. submit demands a clean worktree
(staged/unstaged/untracked rejected with the file list, gitignored
files exempt) whose HEAD descends from the task base, pins an immutable
snapshot, and moves to awaiting_verification. recover requeues tasks on
lease expiry, heartbeat timeout or execution timeout, fences the old
attempt (dead token, quarantined directory), respecting the restart
limit; it is idempotent.

## 7. Workspace isolation

One worktree and branch per execution attempt under
tasks/<task>/attempts/<n>/. The actual diff of a submission is compared
against files_write; violations fail the gate. Fenced attempts move to
quarantine/ and stay inspectable for manual salvage; nothing from an
old attempt is ever trusted as the next attempt's starting point beyond
the last verified submission SHA.

A task with dependencies starts from its dependency base: the accepted
SHAs of its parents, composed in topological order, recorded as
`dependency_base` (`[{task, accepted_commit_sha}]`). Parents that
changed different lines of one file compose; parents that changed
the same lines are a conflict, detected at claim BEFORE the executor
gets a workspace: the claim is rejected naming the file and both
parents, the task stays pending and nothing accepted is touched.
Independent tasks never see each other's files.

## 8. Immutable snapshots

Every submit pins its commit: a git ref
refs/agent-teams/<team>/<task>/<submission-id> protects it from garbage
collection, and SQLite records submission id, SHAs, branch, changed
files and the verification status. Machine checks and verifier review
run against an ephemeral clean worktree at the pinned SHA, never the
executor's live directory. After a PASS the accepted SHA is pinned on
the task; changing branches afterwards cannot alter what integrates.

Version notions of a task (schema v4): `trusted_base_commit` (the run
base or the last ACCEPTED result), `draft_checkpoint_commit` (the
commit a failed attempt left; kept for continuation, never trusted)
and `accepted_commit_sha`. A new attempt may continue from a draft,
but its changes are always judged against the trusted base, so a
violation found earlier cannot be hidden by a retry. Every attempt
records the base it used, bound to a SHA.

A rename or a copy is judged on BOTH of its paths: moving a file out
of a forbidden location is a violation of that location. The git
output is parsed NUL-separated, so names with spaces and non-ASCII
characters are handled correctly.

## 9. Acceptance and verification

The engine runs command-backed criteria itself (test, file_check,
security_check) in the snapshot with a timeout; a failed required
criterion or a write-set violation overrides any PASS into a FAIL. A
PASS verdict additionally requires a structured verification report
(schemas/verification-report.schema.json) validated against the current
submission: ids, commit SHA, full criterion coverage, evidence with
file:line for content criteria, verdict consistency. A lead override
(--override-reason) bypasses the report requirement, is audited, and
still cannot bypass the machine gate. In shared mode a PASS needs no
report (there is no snapshot to bind one to), and a reason given there
is recorded as the same decision. The engine validates reports; it
cannot prove the author was independent.

The engine writes a manual acceptance as two journal events: the
decision of the lead, then the state change it caused. The second has
the type and the shape of a passed check. Whatever reads the journal
for a person (the board does) must show the decision once and never
"check passed" for it.

## 10. Recovery and heartbeat

heartbeat_timeout_s bounds the silence between heartbeats;
execution_timeout_s bounds one attempt's total duration (heartbeats
cannot extend it); the lease is the claim-time reservation refreshed by
heartbeats. Recovery fires on any of the three, is idempotent, preserves
mailbox history, and keeps quarantined work for manual salvage.

## 11. Integration and apply

integrate merges the accepted SHAs in topological DAG order into a
fresh integration branch, optionally running a check command in the
merged tree; conflicts and failed checks stop at integration_failed and
never touch task snapshots or the project branch. apply fast-forwards
the project branch to the integration branch, refusing a dirty project
or a moved base (re-integrate is the documented path); a failed apply
never finishes the run.

## 12. Security model

Enforced: name and path validation, OS-level state lock, hashed tokens,
per-attempt fencing, immutable snapshots, SHA-bound integration,
machine-checked acceptance, hash-chained audit log. Not a boundary: a
local executor with arbitrary shell and filesystem access can read or
edit local data; the mechanisms make the workflow reliable against
mistakes, races and stale executors, not against an attacker on the
machine.

The budget is changed only by the lead, with the lead token, as an
audited decision (section 17); an executor's token cannot move a limit,
and the board has no way to change one.

The board (`team.py ui`, section 16) is read-only by construction: it
opens state.db with `mode=ro` and `PRAGMA query_only`, answers GET
only, and selects a whitelist of columns (a token hash is never read).
The project root is fixed by the command that started the server; a
request can name a team inside it, never a path. While bound to
loopback, a `Host` header that names another machine is refused (DNS
rebinding). The page carries a CSP without any inline or remote
source, and every value from the wire is escaped or forced to a number
before it reaches the markup. It is still a local page without
authentication: whoever sits at the machine reads what the lead reads.

## 13. Observability

Usage from subagent notifications (both telemetry formats) or manual
entry, idempotent by dedup key; absent numbers stay absent. Cost with
quality flags (actual/estimated/unknown) is computed once, when the
record is written, and stored with it: it is the price of the day the
work was done, and a later change of the price table never rewrites
the history. Every reader (`cost`, `metrics`, the report, the board)
reports the stored value. Of the price table's names the longest one
that matches the model wins (`glm-5.3-flash` has its own price, though
`glm-5.3` is a substring of it). Metrics
cover execution (attempts, submissions, first-submit lag, fix time),
verification (first-pass rate, required criteria, violations,
overrides), recovery (timeouts, blocks, quarantine) and integration
(conflicts, applies), with source-quality labels for the speedup
comparison. Self-contained HTML report; idempotent Langfuse export.

## 14. Compatibility and migration

v0.2 teams (JSON files, no state.db) run through team_v02.py. New
engine features arrive through SQLite migrations (schema v3 adds
submissions, accepted SHA, integration SHAs) that never delete history.
The JSON mirrors and the CLI command set stay backward compatible;
strictness changes (clean submit, report requirement) are engine
version 2.0.0 behavior and are documented as such. Schema v4 adds
nullable columns only (`trusted_base_commit`,
`draft_checkpoint_commit`, `dependency_base_json` on tasks;
`evidence_status`, `evidence_notes_json` on submissions); migrations
run one statement at a time and never drop history.

## 15. Testing strategy

Unit (every module), security (path rules, authority battery),
concurrency (real multi-process contention, crash release, killed
transactions), integration (spec scenarios A-F, immutable execution,
recovery and fencing, verification reports, workspace lifecycle, CLI
subprocess smoke with exit codes). The board is tested at four layers:
the reader's words (viewmodel), the read-only snapshot over a real
git-backed team, the HTTP server (raw requests, wrong Host, write
methods, traversal), and the page script itself, run under Node on a
fake document with a hostile snapshot. The budget is tested against a
real team: the gate refuses a start, a running executor is not stopped,
nothing is left behind, the limit can be raised; the numbers are tested
as pure functions (three things never summed into one, a cost that is
not known is never a zero, a recovery that did not work is not shown as
one that did). CI: ubuntu, macos, windows x Python 3.10-3.14 plus
vermin, schema validation and manifest checks; 900+ tests, no LLM or
network in the suite.

## 16. Dashboard (the board)

`team.py ui --team <id> [--host] [--port] [--open]` serves a read-only
board for a reader who does not know the engine's vocabulary. It binds
to 127.0.0.1, prints its address, and runs until interrupted. It stops
on Ctrl-C however it was started: a process a script left in the
background inherits the interrupt as ignored, and the command takes it
back, because it promised to stop on it.

It shows the project, its goal and when the work began; overall
progress in whole tasks (equal weight: the engine has no size estimate);
what is happening now (with the time on the task and the latest word
about it); the specialists (one card each, with its responsibility, its
state, what it is doing and its count of tasks; the busy first); the
plan (each stage with its state, what it waits for, what comes next,
the expected result, and the three checkpoints the engine records:
started, handed over, accepted); what needs the owner's decision (each
item says what happened, why it matters, what the system tried, what is
needed and what happens otherwise); and the activity feed. The clock of
a team stops with its run.

A specialist or a stage opens a sheet with its details and its history:
what the stage is about, what is expected, how it is judged (a machine
check and a judgement of content are told apart), who does it, what is
done, the checks with the checker's remarks. An open sheet stays open
across refreshes and follows the data.

Two levels are built from one snapshot: `simple` (roles and titles, no
identifier longer than a display name, bookkeeping events left out) and
`advanced` (the technical fields beside every human one). Switching the
level reads the same state and changes nothing.

The words are the engine's facts, never a guess:

- A task is "in work" only while its executor answers. One silent for
  longer than the team's own `heartbeat_timeout_s` is shown as not
  answering, in the task, in its next step and in its specialist alike.
- No checkpoint, percentage or count is shown that the engine did not
  record.
- The checker is a specialist too, counted by its checks; a result the
  lead accepted by decision is not counted as the checker's.
- An executor with tasks of different lenses is named, on each task,
  by that task's role.
- A manual acceptance is shown as one decision (section 9).
- The remark of the checker travels with the failed check and with the
  stop it caused, into the feed and into the attention card.
- Counts in the texts come from the task (`verification_failures`,
  `restarts`), not from a sentence written in advance.
- A block that recovery writes and releases in one step is not shown as
  a stop: nothing is waited from the reader.
- A cost that is not known is said to be not known.

The API answers `/api/status` (has anything changed), `/api/snapshot`
(the whole board), `/api/events` (with a cursor: `after` for what is
new, `before` for reading further back), `/api/tasks`,
`/api/tasks/<id>` (one task with its history), `/api/agents`,
`/api/cost`, `/api/metrics`, `/api/recovery`, `/api/integration`,
`/api/artifacts` (the files of every result), `/api/benchmark` (only
for a team that declared one, section 18),
`/api/results/<task>/<file>` (one file of a task's result) and
`/api/health`. The last fields of a snapshot (v2.1 spec 16.3) are read
with the same connection and the same whitelists as the rest. The page polls `/api/status` every 3
seconds and takes a whole snapshot only when something changed (or once
in a while, so its clocks stay true): a refresh stays light.

The plan as a graph (v2.1 spec 6). Tasks run left to right in the order
of their dependencies; the column is the length of the longest chain
before a task, tasks that can run in parallel share a column. The
layout is computed by the engine side (`planview.plan_layout`), is
stable for the same plan and needs nothing from the reader. The reader
zooms (wheel or buttons), drags, searches by title and filters (all,
not completed, problems only); what a filter hides stays in place, only
faded, so the shape does not jump. The position, the zoom and the
filters survive refreshes. In the technical level a node also names its
id and branch. A node opens the sheet of its task.

The timeline (v2.1 spec 7) is drawn from the journal only: every span
of a task runs from one recorded event to the next, and is named by what
the task was doing in between (work, check, rework, waiting, recovery,
done, stopped). A task with no events has no span. Nothing is placed on
the timeline that the engine did not record.

The result of a task (v2.1 spec 8.5) can be opened from its sheet. Only
a file the task's latest hand-over consists of can be read, and only as
that hand-over pinned it: from its commit in a git project, so the
board shows the version that was accepted even after the folder moved
on. Text is served as text (never as a page of the board's own origin),
a picture as a picture, anything else as a download, each within a size
limit, under a policy that lets the file do nothing. In shared mode
(no git) the engine keeps no diff of a result: the files the task
declared are what the board shows.

The activity feed (v2.1 spec 9) is filtered by type, by specialist, by
stage and by time, searched by text, held by a pause that counts what
came in since, and read further back through the `before` cursor. A
line about a stage opens that stage.

Four cards say what the numbers mean (v2.1 spec 10 to 13; the rules
are in section 18): the quality of the results, the spend against the
budget, the problems and how each ended, and how the parts become the
whole result. A fifth one, the comparison of teams, exists only for a
team that declared a benchmark.

A figure always comes with the words that say what it counts, and with
the endings its number needs (1 проверка, 2 проверки, 5 проверок). A bar
gets its width through the style object of the page, never through a
style attribute, which the policy refuses. What waits for the owner
stands first, in the attention card and in the list of problems alike.

On a phone the page is one column; the header stays two short rows (the
name with the goal, then the state with the mode and the theme) because
it is sticky and a taller one hid a quarter of the screen.

The texts of the board are Russian today; localization is an open
decision (BACKLOG).

## 17. Budget control

A team may agree a budget in its config, or set it later as the lead:
`team.py budget --usd <n> --per-task <n> --warn-ratio <0..1>
--lead-token <tok>`. `none` removes a limit; a value the command does
not mention stays as it is. Without the lead token the command only
shows the budget and where the team stands against it. The keys:

- `budget_usd`: the budget of the team; no limit when absent;
- `budget_per_task_usd`: the limit of one task; no limit when absent;
- `budget_warn_ratio`: the share of the budget from which the board
  warns (default 0.8).

The engine enforces the limits at one place, `claim`, before a
workspace is made: when the known spend reached the budget, or the
known spend of the task reached its limit, the claim is refused with
exit code 2 and a message that starts with `BUDGET_LIMIT`. Only new
work is refused: an attempt that is already running is never stopped,
and nothing is left behind, because the check runs before anything is
created. A limit counts what is known: records without a price do not
add to the spend and are named on the board, so the owner sees how far
the limit really reaches. Both sides are compared at the precision of
the price table (six decimal places): a budget that is exactly spent is
spent, whatever the floating point says.

Changing the budget is a decision of the lead and is written to the
journal (`budget_changed`). The board shows three states, decided by
the rule the engine enforces: no budget, within the budget, warning at
the agreed share, limit reached. A spent budget is also a problem of
the recovery centre: the engine refuses the start and writes nothing,
but it is the problem the owner has to answer.

The behaviour at the limit is the one the specification describes
(block new starts, stop nothing running, say why) and is not a setting.

## 18. What the numbers mean

Rules that hold for every figure; the functions are in
`agent_teams/analytics.py`, pure over what the snapshot read.

Quality (v2.1 spec 10). An automatic check of files and commands, a
judgement of content by the checker, and a result the lead accepted by
decision are three different things and are never summed into one
number. Each handed-over result falls in one bucket: passed the check
first time, after rework (a result that passed at first and was then
sent back is one of these), accepted by decision, still being checked.
The checks are shown in the order their answers came back, one mark
each. Every check is read from the latest check of its task; a check
recorded behind a decision of the lead is not a check. A defect is
named by the title of its task.

Spend (v2.1 spec 11). The spend is in USD, by the price of the models
at the moment of work; the board names the currency beside the figures
(a currency of the reader's choice would need an exchange rate the
engine cannot know offline; BACKLOG). Every sum carries its
reliability: the records without a price are counted per team, per
stage and per specialist, a stage with no priced record is "not
defined" and never zero, and a sum with unpriced records says so under
itself. The average per task counts the tasks that started; the price
of an accepted result counts all attempts, rework included. The
technical level adds a block per model: input and output tokens, cache
read and write, the price per 1M tokens the cost was priced by, and
the quality of the price.

Problems (v2.1 spec 12). A problem is read from the journal; what came
of it is read from what followed at once, not from the state of the
task now, so a rework that was handed in again is shown as dealt with
whatever happened later. A problem is not hidden because it repeated,
and a recovery that did not work is not shown as one that did. Each
one says what happened, when, to which stage, what the system did,
what cannot be lost, how it ended, whether a person is needed and what
that person can do. The executor who claimed the task next is the one
who continued the work, by name. A block that recovery writes and lifts
in one step is not a problem. Two types of the specification have no
event of their own in this engine and are shown where the engine does
record them: missing data is a stage waiting for another one (the
attention card), and a clash between results is a problem of the
integration.

Integration (v2.1 spec 13). Three states are kept apart: a result
accepted, the results integrated, the changes applied to the project.
The last two are the run's own record. No commit is shown at the simple
level; the technical level names the integration branch, the base, the
resulting commit, the merged commits and the clash as the engine wrote
it. A clash is not said to be resolving itself: the specification's
example sentence is worded as what is true here, the sources are kept
and the integration has to be started again.

The comparison of teams (v2.1 spec 19). Only teams that declare one
benchmark in their config (`benchmark: {"name", "executors"}`) are
compared, and only under the same name: the same tasks, models and
conditions are the owner's responsibility, the declaration says they
hold. A sentence about speed or money is made only from teams that
finished the whole set (at least two), against the smallest of them;
without them the board says there is nothing to compare and shows the
rows it has. Money is compared only when the spend of both teams is
complete. Attempts, problems and clashes between results are counted
for every team: a decision of the lead is neither a check nor an
attempt.
