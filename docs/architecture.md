# Architecture

Seven logical components (spec section 2), one package
`plugins/agent-teams/skills/agent-teams/scripts/agent_teams/`:

| Component | Module | Responsibility |
|---|---|---|
| Team orchestrator | `engine.py` (lead side), `cli.py` | commands, run lifecycle, the lead is the single integration point |
| Task engine | `statemachine.py`, `storage.py` | statuses, guarded transitions, SQLite state, no lost updates |
| Workspace isolation | `workspaces.py` | per-task git worktrees, diff capture, cleanup |
| Quality gate | `quality.py` | machine-checked criteria, forced FAIL, integration gate |
| Observability | `observability.py`, `langfuse.py`, `report.py` | usage, cost, metrics, export; the report for a manager, built from the board's own snapshot |
| Onboarding | `onboarding.py` | a brief in plain words becomes questions (one at a time), a plan to confirm and a specification the engine validates |
| Recovery | `engine.py` (recover/heartbeat) | expired leases, restart limits, worktree reuse |
| Board | `viewmodel.py`, `dashboard_data.py`, `dashboard_server.py`, `planview.py`, `analytics.py`, `dashboard/` | the engine's state in the reader's words, one read-only snapshot per request, the layout of the plan graph and the timeline, a local page (spec 16), and what the numbers mean (quality, budget, problems, integration, the comparison of teams) |

Team directory: `<project_root>/.agent-teams/<team>/`

    state.db      SQLite (WAL): teams, runs, tasks, agents, messages,
                  verification_attempts, events (hash chain), token_usage, exports
    locks/        OS file lock (.state.lock) serializing all mutations
    backups/      pre-migration backups (newest 10 kept)
    tasks/<id>/workspace/   the task's git worktree
    integration/workspace/  the integration gate's worktree
    tasks.json, inbox/<agent>.json, events.jsonl   v0.2-compatible mirrors

Hardening (v2.0): immutable submissions (pinned git refs + the
submissions table), per-attempt workspaces with quarantine, an
ephemeral verification snapshot, SHA-bound integration with separate
apply, and a run state machine (active, integration_running,
integration_failed, apply_pending, finished). Entry points:
`team.py` (the engine CLI), `team_v02.py` (the legacy
single-file engine for teams created before v0.3).

The board is the only reader that does not go through `engine.op_*`:
those open the database read-write (they may migrate and checkpoint),
so `dashboard_data` opens it strictly read-only and returns one
consistent snapshot; `viewmodel` is the single place that turns tasks,
events and runs into words, and `analytics` the single place that
decides what the numbers mean (quality, budget, problems, integration,
the comparison of teams): pure functions over what the snapshot read.

## Design rules

- Critical constraints are enforced by the engine, not by agent
  discipline (the founding principle of the spec).
- Every mutation: StateLock, then one BEGIN IMMEDIATE transaction, then
  hash-chained events, then mirrors.
- Authority comes from tokens printed once and stored hashed; free-text
  `--by` values are actor records, never authority.
- Numbers the runtime does not provide stay absent, never invented.
- A figure the board shows is computed engine-side, in a pure function
  with a test; the page only puts the words and the endings to it. A
  machine check, a judgement of content and a decision of the lead are
  never summed into one number.
- A limit is enforced at one place, before anything is created, so a
  refused operation leaves nothing behind (the budget and the pause in
  `claim`).
- The board changes nothing by itself: one door (`engine.op_action`)
  leads to the operation that owns the change, under the lock and with
  the lead's authority that the server keeps in memory (v2.1 spec 17).
- The whole journal is never loaded at once: a snapshot carries the
  newest of everything and a count of the rest; a report written once
  asks for all of it (`snapshot(whole=True)`).
- A reader meets one fact once: the journal has bookkeeping lines (the
  archive of a lost attempt, the second half of a manual acceptance),
  and the reader's model names them as notes before anything counts
  them.
