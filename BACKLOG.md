# Backlog

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
