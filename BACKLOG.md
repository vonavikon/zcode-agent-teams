# Backlog

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

## Open, found while building the board (2026-11-25)

- The snapshot's `metrics` were always empty: `collect_metrics` needs
  a store with a live connection, and the adapter's failure was
  swallowed into `{}`. The board does not show metrics yet (M4); build
  them on the snapshot's own data, with a test, instead of reviving
  the adapter.
- The texts of the board are Russian, in a repository whose rule is
  technical English with READMEs in five languages. For the alpha that
  is the language of its first users; before a stable 2.1.0 the strings
  should move out of the code into a dictionary with English as the
  base. The owner decides.
- The board has no authentication and binds to loopback by design. An
  owner who is not at the machine needs a proxy with authentication in
  front of it; not built.
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
