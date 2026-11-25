# Testing

Run from the repository root:

    python -m pytest tests/ -q

Layers:

- `tests/unit/`: every module in isolation (locking, validation, DAG,
  storage and migrations, events and the hash chain, state machine,
  observability, cost pricing (the right model, the cost stored once),
  the board's words (viewmodel), CLI output (nothing lost in text mode,
  pure ASCII in every mode). The board's page script is run under Node
  on a fake document (`page_harness.js`): a hostile snapshot, escaping,
  refresh without redraw, outage states, the mode and theme switches,
  the layout of the plan graph and the timeline over recorded events,
  and the cards that say what the numbers mean (a figure with its
  words, a bar that gets its width without a style attribute, nothing
  of the technical level at the simple one). These tests are skipped
  where Node is absent. The numbers themselves are pure functions
  (`test_analytics.py`): a machine check, a judgement and a decision
  are never summed into one; a cost that is not known is never a zero;
  a recovery that did not work is not shown as one that did; a sentence
  about speed or money needs two finished teams of one declared set
  (`test_benchmark.py`). The budget (`test_budget.py`) is enforced
  against a real team: the gate refuses a start, a running executor is
  not stopped, nothing is left behind, the limit can be raised, and the
  command exits with code 2 on `BUDGET_LIMIT`.
- `tests/security/`: path rules (absolute, traversal, reserved
  directories, symlink escapes, case collisions) and the authority
  battery (forged lead token, executor self-verify, stale execution
  token, wrong owner, verifier-role claim).
- `tests/concurrency/`: real multi-process suites: counter contention
  under the lock, crash release through the OS, killed SQLite
  transactions, concurrent event writers with chain verification.
- `tests/integration/`: spec scenarios A-F end to end, workspace
  isolation on real git repositories, the machine quality gate, the
  integration gate (including a real merge conflict), CLI subprocess
  smoke tests with exit codes. The board: the read-only snapshot over a
  real git-backed team, the HTTP server through raw requests (wrong
  `Host`, write methods, traversal, bounds), the files of a result (the
  accepted version and not the folder's current one, only the files of
  the result, text never as a page, size limits), and `team.py ui` as a
  real process that announces its address and stops on Ctrl-C; and the
  read-only comparison of the teams of one project.

CI (`.github/workflows/ci.yml`): ubuntu, macos and windows times Python
3.10-3.14, plus a vermin check that the engine stays 3.10-compatible.

What is deliberately absent: no test calls an LLM or the network; the
Langfuse uploader is exercised only through its idempotency and error
paths.
