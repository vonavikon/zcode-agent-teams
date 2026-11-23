# Task state machine

Statuses: `pending`, `in_progress`, `awaiting_verification`, `completed`,
`blocked`, `cancelled`. `completed` and `cancelled` are terminal; a
completed task is never reopened (start a new task or a new run).

| From | To | Initiator |
|---|---|---|
| pending | in_progress | executor (claim), lead |
| pending | cancelled | lead |
| in_progress | awaiting_verification | executor (submit) |
| in_progress | blocked | recovery, lead |
| awaiting_verification | in_progress | gate (FAIL below the limit) |
| awaiting_verification | completed | gate (PASS) |
| awaiting_verification | blocked | gate (FAIL at the limit) |
| blocked | pending | lead (release), recovery |

Every transition (`agent_teams/statemachine.py`):

1. checks the edge and the initiator,
2. updates the row with a guard on the previous status (a concurrent
   writer becomes a visible rejection instead of a lost update),
3. resets the on-enter fields (claim timestamps on claim, clears the
   execution token on completion/blocking, and so on),
4. appends one hash-chained event with the actor and the reason.

Run statuses (v2.0): active -> integration_running -> (failed ->
integration_failed | ok -> apply_pending) -> finished; apply is a
separate command and a failed apply never finishes the run.

The verification limit counts failed verdicts immediately: with
`max_verification_failures: 2` (default) the second FAIL blocks the
task. The v0.2 engine blocked on the third FAIL; the off-by-one was
fixed in v0.3.0.
