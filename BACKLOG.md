# Backlog

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
