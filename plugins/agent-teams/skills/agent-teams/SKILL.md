---
name: agent-teams
description: Run a team of agents in ZCode when the user asks for "agent teams", "a team of agents", "waves of executor + verifier", "review in N lenses", "competing hypotheses" or "parallel implementation by layers". Parallel executors get isolated git worktrees, machine-checked acceptance criteria, an integration gate, run metrics and a Langfuse export. Also for the Russian wording: «командой агентов», «волны исполнитель + верификатор», «ревью в N линз», «параллельная реализация по слоям».
---

# Agent Teams for ZCode (v1.0)

A port of the Claude Code Agent Teams approach with the acceptance loop
enforced by the engine, not by discipline. The lead is the main session
(you); teammates and verifiers are subagents; all state lives in SQLite
under `.agent-teams/<team>/` with v0.2-compatible JSON mirrors.

## Quick start

1. **Plan**: write the tasks spec (schema below) and check it:
   `python <skill>/scripts/team.py plan --tasks tasks.json --project-root <root>`
   Non-overlapping `files_write`, verifiable `acceptance`, 2 to 5
   teammates, 1 to 3 tasks each.
2. **init**: `python <skill>/scripts/team.py init --team <id> --tasks tasks.json --project-root <root>`
   Prints the **lead token once**: keep it in the lead session; every
   verdict and intervention requires it.
3. **Spawn teammates**: Agent tool, one per teammate,
   `run_in_background: true`, prompt from `references/prompt-teammate.md`.
   Each claim prints an **execution token** that the executor passes to
   submit.
4. **Gate**: on every `VERIFICATION REQUEST` spawn a verifier
   (`references/prompt-verifier.md`, read-only). The engine itself runs
   the command-backed criteria; your verdict still needs the lead token:
   - PASS -> the verifier's answer is one JSON object. Save it to a file
     and pass it: `team.py verify --task <tid> --verdict pass --report report.json --lead-token <tok>`
   - PASS by your own decision, without a report, only with the reason:
     `... --verdict pass --override-reason "<why>" --lead-token <tok>`.
     It is audited, and the board shows it as a manual acceptance,
     never as a passed check.
   - FAIL -> `... --verdict fail --feedback "<what exactly>" --lead-token <tok>`
   A failed required machine criterion or a write outside `files_write`
   overrides PASS into FAIL automatically. The second failed round (by
   default) blocks the task: `release` it or handle it yourself.
5. **Integrate**: `team.py integrate --team <id> --lead-token <tok> [--check CMD] [--apply]`
   merges the accepted branches, runs the integration check, refuses to
   finish the run on conflicts or regressions.
6. **Finish**: `team.py status` (all completed), `report --out run.html`,
   `metrics`, optionally `langfuse --name "<run name>"`. Give the owner
   the summary and the report path.
7. **Board** (any time after init): `python <skill>/scripts/team.py ui --team <id> --project-root <root>`
   prints an address and serves a read-only board in the browser: the
   plan as a graph and as a list, how the time went, what each
   specialist is doing, what waits for the owner's decision and why, in
   plain words, and the files of every result as they were accepted.
   It also says what the numbers mean: how far the results can be
   trusted (a machine check, the checker's judgement and a decision of
   yours are never summed into one), the spend against the budget, the
   problems and how each ended, how the parts become the whole result.
   Offer it when the owner wants to watch or is not technical. `--open`
   opens the browser; it stops with Ctrl-C. Nothing on the board can
   change the run.
8. **Budget** (when the owner agrees one): put it in the spec's config
   (`budget_usd`, `budget_per_task_usd`, `budget_warn_ratio`) or set it
   as the lead:
   `python <skill>/scripts/team.py budget --team <id> --project-root <root> --usd 10 --per-task 2.5 --lead-token <tok>`
   Without the token the command only shows the budget and the spend.
   When the budget is spent, every new `claim` is refused with exit
   code 2 and `BUDGET_LIMIT` in the message: running executors are not
   stopped, nothing is left behind. Tell the owner and raise the limit
   only on the owner's word; the board shows the budget as exhausted
   and names it among the problems that wait for the owner.

## Task schema

```json
{
  "title": "Security review of the billing service",
  "goal": "Find what an attacker could reach in billing and say what to fix first",
  "config": {"workspace": "auto", "max_verification_failures": 2,
             "budget_usd": 10, "budget_per_task_usd": 2.5, "budget_warn_ratio": 0.8},
  "agents": [{"id": "sec-lens", "role": "executor"}],
  "tasks": [
    {
      "id": "lens-security",
      "title": "security lens review",
      "description": "Read the auth and payment paths, list findings with file:line",
      "assignee": "sec-lens",
      "lens": "security",
      "files_read": ["src/auth.py"],
      "files_write": ["reports/security.md"],
      "acceptance": [
        {"id": "AC-001", "description": "findings are evidenced",
         "type": "code_review", "required": true},
        {"id": "AC-002", "description": "report lints clean",
         "type": "test", "verification": "python -m doctest reports/security.md"}
      ],
      "deps": []
    }
  ]
}
```

`title`, `goal` and a task's `description` are optional words for people:
the board shows them to the owner as they are, so write them for the
owner, not for the engine.

Acceptance types: `test`, `file_check`, `security_check` (each requires a
`verification` shell command the engine runs in the task workspace),
`code_review`, `documentation`, `manual` (human or verifier judgement).
Plain strings are still accepted as manual criteria.

The budget keys are optional (no limit without them): `budget_usd` is
the budget of the team, `budget_per_task_usd` the limit of one task,
`budget_warn_ratio` the share from which the board warns (0 to 1, 0.8
by default). To compare teams of different sizes on one set of tasks,
each of them declares it: `"benchmark": {"name": "<set name>",
"executors": <n>}`. Only teams with the same name are compared, and a
sentence about speed or money is made only from teams that finished
the set; use the same tasks, models and conditions for all of them, or
the comparison is worth nothing.

## Commands (team.py)

Lifecycle: `init`, `claim --by`, `submit --token`, `verify --verdict
--lead-token`, `release`, `cancel`, `recover`, `heartbeat`.
Inspection: `status`, `inspect`, `graph`, `agents`, `logs`, `diff`,
`workspace`, `plan`.
Economics: `tokens`, `usage --notification FILE`, `cost`, `metrics`,
`report --out FILE`, `langfuse`.
Budget: `budget [--usd n | none] [--per-task n | none] [--warn-ratio r]
[--lead-token]`.
Integration: `integrate [--check CMD] [--apply]`.
Mailbox: `message`, `read`. Board: `ui [--host] [--port] [--open]`.
All commands take `--project-root` and `--json`.

`team.py budget` without the lead token shows the limits, the known
spend and the state (no budget, within, warning, limit reached) as the
board decides them.

## Rules for the lead

- Call `team.py recover --team <id> --lead-token <tok>` in your loop:
  every time a teammate notification arrives or you are about to wait,
  recover first. It requeues tasks of silent executors (heartbeat and
  execution timeouts), fences their tokens and quarantines their
  workspaces; it is idempotent and a no-op when everyone is alive.

- The lead does not implement: it plans, verifies and integrates.
- The lead token is printed once; if lost, start a new run. Never paste
  it into teammate prompts.
- Do not change `files_read` of active tasks: reviewers check line
  numbers against the current files.
- A first run of a new configuration is a research or review run.
- Sequential work, one file, many dependencies: work alone or with one
  subagent; a team pays off with 2 to 5 independent file zones.
- After each subagent completion notification, record its `<usage>`
  block: `usage --notification FILE --task <tid> --agent <name>`.

## Pitfalls

- A token that starts with a dash (teams made before 2.1.0a3): write it
  glued to its option, `--lead-token=<tok>` or `--token=<tok>`.
- Windows / Git Bash: absolute paths in `--project-root`.
- Non-git projects fall back to `shared` workspaces: no isolation, warned
  at init.
- `team_v02.py` runs old teams created before v0.3 (no state.db).
- The round limit blocks on the Nth failed verdict exactly
  (`max_verification_failures`, default 2).
- `BUDGET_LIMIT` on a claim is not a failure of the executor: the budget
  or the task's limit is spent. The board names the records without a
  price, which the limit does not count.
- A comparison of teams is shown only for a declared benchmark, and
  says "nothing to compare" until two teams finished: do not present a
  guess as a measurement.
- Trace ids in Langfuse are strictly 32 hex: use the engine's export.
