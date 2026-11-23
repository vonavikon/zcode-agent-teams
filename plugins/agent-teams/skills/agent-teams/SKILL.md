---
name: agent-teams
description: Run a team of agents in ZCode when the user asks for "agent teams", "a team of agents", "waves of executor + verifier", "review in N lenses", "competing hypotheses" or "parallel implementation by layers". Parallel executors get non-overlapping files, an acceptance gate with verifiers (exit-2 loop), mailbox messages between agents and a run report in Langfuse. Also for the Russian wording: «командой агентов», «волны исполнитель + верификатор», «ревью в N линз», «параллельная реализация по слоям».
---

# Agent Teams for ZCode

A port of the Claude Code Agent Teams approach to ZCode mechanics. The lead is the main session (you), teammates are subagents started through the Agent tool (`run_in_background: true`), coordination goes through files on disk, acceptance is done by verifiers.

How the mechanics map:

| Claude Code | Here |
|---|---|
| shared task list + file locking on claim | `.agent-teams/<team>/tasks.json` + a single state lock `locks/.state.lock` (O_EXCL, all mutations are serialized) |
| mailbox messages between agents | `.agent-teams/<team>/inbox/<agent>.json` |
| `TaskCompleted` hook with exit 2 ("not accepted, continue") | verifier subagent + `team.py verify --verdict fail --feedback ...` |
| team trace | `team.py langfuse` -> trace + a span per task + a score per verdict |

## Quick start

1. **Describe the tasks**: a JSON file (see the schema below). The main rules: tasks have non-overlapping `files_write` (two agents in one file means overwrites), and each task has verifiable `acceptance` criteria. Rules of thumb: 2 to 5 teammates, 1 to 3 tasks per teammate.
2. **init**: `python <skill>/scripts/team.py init --team <id> --tasks tasks.json --project-root <root>`
3. **Spawn teammates**: Agent tool, one per teammate, `run_in_background: true`. The prompt comes from `references/prompt-teammate.md` with absolute paths to `team.py`, the project root and the team directory, plus the ids of the teammate's tasks.
4. **Acceptance gate**: a teammate ends its final message with `VERIFICATION REQUEST <task-id>` and a summary per criterion. For each request spawn a verifier (prompt `references/prompt-verifier.md`, read-only). Verdict:
   - PASS -> `team.py verify --team <id> --task <tid> --verdict pass`
   - FAIL -> `team.py verify ... --verdict fail --feedback "<what exactly was not accepted>"`. The feedback lands in the executor's mailbox by itself; then SendMessage to the executor: "read your mailbox, fix it, request verification again". On the third failed verification the engine moves the task to `blocked` (ROUND LIMIT): bring it back with `release` or handle it yourself.
5. **Finish**: `python <skill>/scripts/team.py status --team <id>`: make sure everything is `completed`. Then `team.py langfuse --team <id> --name "<run name>"` (needs the env vars `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`). Give the owner the summary and the trace link.

## Task schema

```json
{
  "tasks": [
    {
      "id": "lens-security",
      "title": "security lens review",
      "assignee": "sec-lens",
      "lens": "security",
      "files_read": ["src/auth.py"],
      "files_write": ["reports/security.md"],
      "acceptance": ["every finding has a file:line reference", "no irrelevant findings"],
      "deps": []
    }
  ]
}
```

## team.py commands

- `init --team <id> --tasks <file>`: create a team
- `claim --team <id> --task <tid> --by <agent>`: claim a task (under the shared state lock, dependencies are checked)
- `verify --team <id> --task <tid> --verdict pass|fail [--feedback ...]`: the gate verdict. fail puts the feedback into the executor's mailbox. On the third failed verification the task goes to `blocked` (ROUND LIMIT): after that only `release` or the lead's own handling
- `release --team <id> --task <tid>`: return a task to pending (after ROUND LIMIT or an agent failure)
- `tokens --team <id> --task <tid> --add N`: add an agent's tokens to a task (the lead calls it after each notification that a subagent finished; visible in status and in Langfuse)
- `message --team <id> --to <agent> --by <agent> --text "..."` / `read --team <id> --for <agent>`: the mailbox
- `status --team <id>`: statuses, failed verification rounds, tokens, unread messages
- `langfuse --team <id> [--name ...]`: upload the run (trace + a span per task + a score per verdict + total tokens)

Every command accepts `--project-root` (default `.`): pass the absolute path to teammates. Team, agent and task names: only `A-Za-z0-9._-`, up to 64 characters (the engine rejects the rest; this is the path traversal guard).

## Recipes

- **Review in N lenses**: one object, each lens has its own output file. The verifier checks that findings are evidenced (the file:line exists and says what is claimed).
- **Competing hypotheses**: N investigators of one problem, each with its own hypothesis in `assignee`, inputs are independent, the debate is a separate task that reads all the reports. Guards against anchoring on the first hypothesis.
- **Parallel implementation**: a split by layers or modules, tasks have non-overlapping `files_write`, `deps` set the order of waves.

## Rules for the lead

- The lead does not implement: it dispatches, verifies and summarizes. If you started writing code for a teammate, stop and respawn.
- While the team works, do not change files from the tasks' `files_read`: reviewers check line numbers against the current file, and your line shift breaks their references. The first run caught exactly this (see examples/demo-run).
- The first run of a new configuration is a research or review run, not parallel code writing.
- Sequential tasks, edits to one file, many dependencies: a team is not needed, work yourself or with one subagent. Practical threshold: a task under about 30 minutes of manual work, or one that writes to a single file, goes to one subagent. A team pays off on objects with 2 to 5 independent file zones.
- An overlap of `files_write` between two tasks is forbidden. init does not check it for you: check it when you compose tasks.json.
- Tokens: after each notification that a subagent finished, call `team.py tokens --task <tid> --add <subagent_tokens>`. Without it the Langfuse report shows the verdicts but not the cost.

## Pitfalls

- Windows / Git Bash: pass absolute paths in `--project-root`, do not rely on the subagent's cwd.
- The trace id in Langfuse is strictly 32 hex characters: `team.py` already does `hex32`, do not invent your own format.
- Self-hosted Langfuse v4 in events_only mode: the legacy `/api/public/ingestion` accepts only score events. `team.py langfuse` sends traces through the OTLP endpoint `/api/public/otel/v1/traces` and scores through `/api/public/scores`. Reading back through REST was removed: view the trace in the UI or through langfuse-MCP.
- Mailbox mutations run under the shared state lock (since v0.2.0), but messages are designed for one recipient: build broadcasts through the lead.
- Tokens: each teammate has its own full context, so a team costs linearly more than one session. Keep tasks large and the team at 2 to 5 agents.
