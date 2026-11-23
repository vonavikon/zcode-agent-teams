# Verification and the quality gate

Flow: executor `claim` (gets the execution token) -> work -> `submit`
(token checked, status `awaiting_verification`, diff captured) ->
verifier subagent reads the criteria and the diff -> lead `verify`
(requires the lead token).

The engine runs every command-backed criterion itself (`test`,
`file_check`, `security_check` types) in the task workspace with a 120 s
timeout. Verdict computation:

- a required criterion that failed, or a write-set violation recorded at
  submit, overrides a PASS into a FAIL with explicit feedback
  ("Quality Gate override: ..."),
- a FAIL below `max_verification_failures` returns the task to
  `in_progress` with feedback in the executor's mailbox; the execution
  token stays valid for the same attempt,
- a FAIL at the limit blocks the task (`ROUND LIMIT` message); the lead
  decides: `release` (fresh verification cycle, execution retry limit
  applies), redo manually, or `cancel`,
- every attempt stores the full criterion report in
  `verification_attempts`.

Integration gate (`integrate`, lead token): merges the accepted task
branches into a fresh integration branch, optionally runs `--check CMD`
in the merged tree. Conflicts and failed checks leave the run active and
the project branch untouched; `--apply` merges the integration branch
into the project branch on success.
