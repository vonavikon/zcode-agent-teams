# Architecture

Seven logical components (spec section 2), one package
`plugins/agent-teams/skills/agent-teams/scripts/agent_teams/`:

| Component | Module | Responsibility |
|---|---|---|
| Team orchestrator | `engine.py` (lead side), `cli.py` | commands, run lifecycle, the lead is the single integration point |
| Task engine | `statemachine.py`, `storage.py` | statuses, guarded transitions, SQLite state, no lost updates |
| Workspace isolation | `workspaces.py` | per-task git worktrees, diff capture, cleanup |
| Quality gate | `quality.py` | machine-checked criteria, forced FAIL, integration gate |
| Observability | `observability.py`, `langfuse.py`, `report.py` | usage, cost, metrics, export, HTML report |
| Recovery | `engine.py` (recover/heartbeat) | expired leases, restart limits, worktree reuse |
| Board | `viewmodel.py`, `dashboard_data.py`, `dashboard_server.py`, `planview.py`, `dashboard/` | the engine's state in the reader's words, one read-only snapshot per request, the layout of the plan graph and the timeline, a local page (spec 16) |

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
events and runs into words.

## Design rules

- Critical constraints are enforced by the engine, not by agent
  discipline (the founding principle of the spec).
- Every mutation: StateLock, then one BEGIN IMMEDIATE transaction, then
  hash-chained events, then mirrors.
- Authority comes from tokens printed once and stored hashed; free-text
  `--by` values are actor records, never authority.
- Numbers the runtime does not provide stay absent, never invented.
