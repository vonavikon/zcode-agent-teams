# Demo run of v1.0 (2026-11-23)

Three concurrent executors (sec, perf, cov) through the real CLI on a
scratch git project: per-task worktrees, machine-checked file criteria,
one honest FAIL round on lens-security (findings without a file:line
reference) fixed and resubmitted within the same attempt, the
integration gate with a check command, and `--apply` into the project
branch.

Honest scope: the executors are plain processes following the teammate
contract, not LLM subagents (the model provider was rate-limited at demo
time). The engine path is identical: tokens, worktrees, diff control,
the machine gate and the integration gate all ran for real.

Key numbers (metrics.json): 4 verification attempts, pass rate 0.75,
tokens 114000, cost $0.2052 (estimated), wall 1.25 s versus a 2.85 s
sequential estimate, speedup 2.29.

Files: tasks.json (spec), init.txt (lead token redacted), status.json,
metrics.json, integration.txt, mailbox-sec-after-fail.json (the executor
saw the FAIL feedback), demo_run.py (the driver).
