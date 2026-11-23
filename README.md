# agent-teams: agent teams for ZCode

A port of the [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) approach to ZCode mechanics: several agents work on one code base from a shared task list, and acceptance is part of the loop.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**English** · [Русский](README.ru.md) · [中文](README.zh.md) · [Español](README.es.md) · [Français](README.fr.md)

## What is inside

- **Shared task list**: `.agent-teams/<team>/tasks.json` holds statuses, dependencies and file sets. Every mutation runs under a single state lock and is written atomically (since v0.2.0)
- **Mailbox messages**: agents write to each other directly, without going through the lead: `.agent-teams/<team>/inbox/<agent>.json`
- **Event log and locks**: `events.jsonl` records every status transition (it is the source for the Langfuse report). `locks/` holds the state lock (O_EXCL; stale locks are reclaimed automatically)
- **Round limit**: a task that keeps failing verification goes to `blocked` and comes back to the lead through `release`. In v0.2.x the block happens on the third failed verification (see [BACKLOG.md](BACKLOG.md))
- **Acceptance gate (exit-2 loop)**: an executor does not close its own task. A verifier subagent checks each criterion against the actual files. FAIL puts feedback into the executor's mailbox and the work continues
- **Langfuse analytics**: `team.py langfuse` uploads the run: one trace per team, one span per task, one score per verdict. Rounds before passing, time per lens and cost are visible in the UI

Roles: the lead is the main ZCode session (it plans, spawns and summarizes), teammates are subagents, verifiers are read-only subagents.

## Installation

In ZCode: Plugin Marketplace → Add → Add Plugin Marketplace → point to the directory of this repository (marketplace.json is in the root). Then Personal → zcode-agent-teams → agent-teams → Install.

The skill appears in the list as `agent-teams`. The Langfuse report needs the environment variables `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST`. Self-hosted instances work: traces go to the OTLP endpoint `/api/public/otel/v1/traces` and scores to `/api/public/scores`, so the events_only mode is enough.

## How it works

```
lead: tasks.json (non-overlapping files_write, acceptance criteria)
  └─ team.py init
  └─ spawn teammates (Agent tool, background)      ← 2-5 agents
       └─ teammate: claim → work → "VERIFICATION REQUEST"
            └─ lead spawns a verifier (read-only)
                 ├─ PASS → team.py verify --verdict pass
                 └─ FAIL → verify --verdict fail --feedback "..."
                      └─ feedback to mailbox → SendMessage to executor → fix
  └─ team.py status → all completed
  └─ team.py langfuse → trace + spans + scores
```

## Recipes

- **Review in N lenses**: security, performance and coverage over one object, each lens writes to its own output file
- **Competing hypotheses**: N independent investigators of one bug, inputs do not overlap, the debate is a separate task. Guards against anchoring on the first hypothesis
- **Parallel implementation by layers**: a split into non-overlapping files, `deps` set the order of waves

## When a team is not needed

Sequential tasks, edits to one file, many dependencies: one session or one subagent is cheaper. Tokens grow linearly with each teammate.

## Structure

```
plugins/agent-teams/
  .zcode-plugin/plugin.json          plugin manifest
  skills/agent-teams/SKILL.md         lead playbook
  skills/agent-teams/references/      teammate and verifier prompts
  skills/agent-teams/scripts/team.py  engine (stdlib-only, Python 3.10+)
marketplace.json                     installation catalog
examples/tasks-review.json           task example (review in 3 lenses)
examples/demo-run/                   artifacts of the first run
```

## License

MIT
