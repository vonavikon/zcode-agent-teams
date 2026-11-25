# agent-teams engine specification

This document is the single source of truth for the engine's behavior.
README files describe the product, SKILL.md the lead's playbook, docs/
the mechanism details; when they disagree with this file, this file wins.

## Versioning

| Artifact | Where | Current |
|---|---|---|
| Plugin | plugin.json, marketplace.json | 2.1.0a1 |
| Engine | agent_teams/version.py ENGINE_VERSION | 2.1.0a1 |
| Task specification format | agent_teams/version.py SPEC_VERSION | 1.0 |
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
observability (usage, cost, metrics), report (HTML), langfuse (export).
The v0.2 single-file engine remains as team_v02.py for legacy teams.

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

JSON with optional config and agents, and a non-empty tasks array
(schemas/tasks.schema.json). Validated before creation: unique ids,
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
still cannot bypass the machine gate. The engine validates reports; it
cannot prove the author was independent.

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

## 13. Observability

Usage from subagent notifications (both telemetry formats) or manual
entry, idempotent by dedup key; absent numbers stay absent. Cost from a
price table with quality flags (actual/estimated/unknown). Metrics
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
subprocess smoke with exit codes). CI: ubuntu, macos, windows x Python
3.10-3.14 plus vermin, schema validation and manifest checks; 300+
tests, no LLM or network in the suite.
