# agent-teams: agent teams for ZCode

A port of the [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) approach to ZCode, with the acceptance loop enforced by the engine instead of agent discipline: several agents work in isolated git worktrees over one code base, every result passes a machine-checked gate before integration.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**English** · [Русский](README.ru.md) · [中文](README.zh.md) · [Español](README.es.md) · [Français](README.fr.md)

## What is inside

- **State machine and SQLite state**: every task status change is a guarded, hash-chained transition in `state.db`; no lost updates, full audit trail, automatic backups and migrations
- **Isolated workspaces**: one git worktree and branch per task, base commit recorded, uncommitted work survives crashes and releases
- **Write-set control**: the actual diff is compared against the task's `files_write`; any write outside the set fails the gate
- **Immutable execution**: submit demands a clean worktree, pins an immutable snapshot (git ref + SQLite), and verification runs against that snapshot; what passed is byte-for-byte what integrates
- **Machine acceptance gate**: criteria with a verification command are run by the engine itself; a failed required criterion overrides a PASS verdict. A PASS additionally requires a structured verification report (or an audited lead override). The executor never verifies its own work: verdicts require the lead token, submissions the execution token
- **Integration gate and apply**: accepted commit SHAs merge in DAG order into an integration branch with an optional check; apply is a separate fast-forward step that refuses a dirty project or a moved base
- **Recovery and fencing**: lease, heartbeat and execution timeouts; lost attempts are fenced (dead token, quarantined workspace kept for salvage) and requeued within restart limits
- **Observability**: token usage from subagent notifications, cost with explicit quality flags, run metrics including the multi-agent vs single-agent comparison, a self-contained HTML report and an idempotent Langfuse export

Roles: the lead is the main ZCode session; teammates and verifiers are subagents (role profiles ship in `agents/`).

## Installation

ZCode → Plugin Marketplace → Add → Add Plugin Marketplace → point to the directory of this repository (marketplace.json is in the root). Then Personal → zcode-agent-teams → agent-teams → Install.

The skill appears as `agent-teams`. The engine needs Python 3.10+ and nothing else (stdlib only). The Langfuse export additionally needs `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and optionally `LANGFUSE_HOST` (self-hosted works; traces go to the OTLP endpoint, so the events_only mode is enough).

## How it works

```
lead: tasks.json (validated: DAG, paths, write overlaps)
  └─ team.py init                     prints the LEAD TOKEN once
  └─ spawn teammates (Agent tool, background)      ← 2-5 agents
       └─ teammate: claim (execution token, worktree) → work → submit
            └─ lead spawns a verifier (read-only) on each VERIFICATION REQUEST
                 ├─ engine runs command-backed criteria itself
                 ├─ PASS → team.py verify --verdict pass --lead-token ...
                 └─ FAIL → verify --verdict fail --feedback "..." → executor fixes
                      (2nd FAIL by default → blocked → release or handle)
  └─ team.py integrate --lead-token ... [--check CMD] [--apply]
  └─ team.py status → all completed; report --out run.html; metrics; langfuse
```

## Recipes

- **Review in N lenses**: security, performance and coverage over one object, each lens writes its own output file
- **Competing hypotheses**: N independent investigators of one bug, the debate is a separate task. Guards against anchoring on the first hypothesis
- **Parallel implementation by layers**: a split into non-overlapping files, `deps` set the order of waves, the integration gate merges the result

## When a team is not needed

Sequential tasks, edits to one file, many dependencies: one session or one subagent is cheaper. Tokens grow linearly with each teammate; check `metrics` for the actual speedup.

## Structure

```
plugins/agent-teams/
  .zcode-plugin/plugin.json          manifest (skills + agents)
  agents/                            executor and verifier role profiles
  skills/agent-teams/SKILL.md        the lead playbook
  skills/agent-teams/references/     teammate and verifier prompts
  skills/agent-teams/scripts/
    team.py                          engine CLI (v1.0)
    team_v02.py                      legacy v0.2 engine for old teams
    agent_teams/                     the package (engine, storage, locks,
                                     validation, workspaces, quality,
                                     observability, report, langfuse)
docs/                                architecture, state machine, security,
                                     workspace isolation, verification,
                                     observability, cost model, testing,
                                     troubleshooting
schemas/tasks.schema.json            JSON Schema for the task spec
tests/                               unit, security, concurrency (real
                                     processes), integration (scenarios A-F)
marketplace.json                     installation catalog
examples/                            task examples and run artifacts
```

## Compatibility

Teams created by the v0.2 engine (JSON files without `state.db`) keep working through `team_v02.py`. New teams use `team.py` and the SQLite engine. The v0.2 mirrors (`tasks.json`, `inbox/`, `events.jsonl`) are still written for every mutation.

## License

MIT
