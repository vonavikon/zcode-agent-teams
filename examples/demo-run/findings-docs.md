# lens-docs: documentation review (SKILL.md + README.md)

Task lens-docs, round 2. Lens: can a foreign agent (and a human) start a team from SKILL.md and README.md without extra explanations.

Method: every command and path from SKILL.md and README.md was checked against the actual repository files and the argparse parser of team.py; the commands `read`, `claim` and `status` were run live against the demo-review team. After round 1 the lead rewrote the Langfuse exporter in team.py and updated SKILL.md:76: both files were re-read and all references of this report were checked against the current versions (in round 1 some references pointed to an older team.py, now fixed).

## What was checked and matches

- All 7 commands from SKILL.md:50-55 (`init`, `claim`, `verify`, `message`, `read`, `status`, `langfuse`) exist in team.py:319-364, the flags match, `--verdict` is restricted to `pass|fail` (team.py:335), `--feedback` is required on fail (team.py:150-151).
- SKILL.md:57 "every command accepts `--project-root` (default `.`)" is confirmed: the flag is declared in all seven subparsers (team.py:322, 329, 338, 346, 352, 357, 363).
- The paths from SKILL.md:22-23 and README:47-56: `plugins/agent-teams/.zcode-plugin/plugin.json`, `skills/agent-teams/SKILL.md`, `skills/agent-teams/references/prompt-teammate.md`, `skills/agent-teams/references/prompt-verifier.md`, `skills/agent-teams/scripts/team.py`, `marketplace.json`, `examples/tasks-review.json`: all exist.
- README.md:54 "task example (review in 3 lenses)": examples/tasks-review.json really has 3 tasks (lens-code, lens-robust, lens-docs).
- README.md:16 "Personal -> zcode-agent-teams -> agent-teams -> Install": the names match: marketplace `zcode-agent-teams` (marketplace.json:2), plugin `agent-teams` (marketplace.json:5), skill `agent-teams` (SKILL.md:2).
- Team directory structure: `.agent-teams/<team>/tasks.json`, `locks/<task-id>.lock` (O_EXCL, team.py:122-124), `inbox/<agent>.json` (team.py:180): matches SKILL.md:14-15 and README.md:7-8.
- SKILL.md:17 and README.md:10: `langfuse` sends a trace per team (a root span with the attribute `langfuse.internal.is_app_root`, team.py:266-279), a span per task (team.py:280-295) and a score per verdict (team.py:300-307).
- SKILL.md:76 about the Langfuse endpoints matches the code: traces go through `/api/public/otel/v1/traces` (team.py:296), scores through `/api/public/scores` (team.py:302).
- SKILL.md:70 "init does not check the files_write overlap" matches the code: cmd_init (team.py:70-106) does no such check.
- SKILL.md:75 "the trace id is strictly 32 hex, team.py already does hex32" matches team.py:31-33.
- SKILL.md:77 "the mailbox is read-modify-write without locks" matches team.py:163-170, 180-186.
- "stdlib-only" for team.py is true: the imports come only from the standard library (team.py:11-22).
- On FAIL the feedback is placed into the executor's mailbox automatically (SKILL.md:26, README.md:9): matches team.py:162-170.

## CRITICAL

None. All commands and paths of the quick start in SKILL.md:19-27 are reproducible: init/claim/verify/message/read/status/langfuse match the actual CLI, and the prompt files for spawning teammates and the verifier are in place.

## MAJOR

### M1. SKILL.md:26: "at most 2 rounds, then the lead takes the task": there is no mechanism to take it

After the second FAIL the lead, following the instructions, is stuck. `verify --verdict fail` does not change the task status (team.py:157-172, the status stays `in_progress`), and `claim` accepts a task only in the status `pending` (team.py:116-117: `if t["status"] != "pending"`). team.py has no command to reset the status or reassign the executor. In other words, there is nothing to "take the task with": none of the commands from SKILL.md:50-55 does that.

Suggested edit: in SKILL.md:26 replace the tail "at most 2 rounds, then the lead takes the task" with a concrete action, for example: "At most 2 rounds; after that the lead edits `.agent-teams/<team>/tasks.json` by hand (status -> `pending`, and remove `locks/<task-id>.lock` if needed) and takes the task itself, or closes it with verify pass and a note in the feedback". The alternative is adding a `reset`/`reassign` command to team.py and listing it in SKILL.md:50-55.

## MINOR

### m1. SKILL.md:27 and README.md:18: LANGFUSE_HOST is declared required, in the code it is optional

Both places say "the env vars `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` are needed". In the code the exit happens only when the two keys are missing (team.py:259-260), while `LANGFUSE_HOST` defaults to `http://127.0.0.1:3100` (team.py:258). An agent following the docs will treat a missing HOST as a configuration error, although a localhost run with the default works.

Suggested edit: in both places say "you need `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`; `LANGFUSE_HOST` is optional and defaults to `http://127.0.0.1:3100`".

### m2. README.md:55: examples/demo-run/ is listed in the structure, but the directory does not exist in the repository

The tree in "Structure" lists `examples/demo-run/   artifacts of the first run`, but the directory does not exist (verified by walking the repository files). For a fresh clone the path is broken. At the time of this review the run is exactly what creates the findings-*.md files in that directory.

Suggested edit: commit the run artifacts (findings-code.md, findings-robust.md, findings-docs.md) after the demo finishes, and the line becomes true. If the artifacts are not meant to be committed, remove the line from README.md:55.

### m3. README.md:58-60: the "License" section declares MIT, but there is no LICENSE file

The section "## License / MIT" declares a license, but there is no LICENSE (or COPYING) file in the repository, verified by walking it. For a plugin that lands on a public marketplace the license text is needed: without it the "MIT" statement grants no distribution rights.

Suggested edit: add a LICENSE file with the MIT text to the root (Copyright (c) 2026 vonavikon) and, optionally, the field `"license": "MIT"` in marketplace.json and plugin.json. Or remove the section if licensing is not decided yet.

### m4. README.md:7-8: "What is inside" does not list locks/ and events.jsonl

The section describes `tasks.json` and `inbox/`, but the engine also creates `locks/<task-id>.lock` and `events.jsonl` in the same directory (team.py:53-58, 101-102). A lead who looks into the team directory guided by the README will see undocumented files. SKILL.md:14 mentions locks/, events.jsonl is mentioned nowhere in the docs.

Suggested edit: add a line to README.md:7-8 like: "`locks/`: claim locks (one file per task), `events.jsonl`: the event log, the source of the Langfuse report".

### m5. README.md:18: "only ingestion is used" disagrees with the code and SKILL.md:76

README.md:18: "the events_only mode is enough, only ingestion is used". There is no call to `/api/public/ingestion` in team.py: traces go through the OTLP endpoint `/api/public/otel/v1/traces` (team.py:296), scores through a separate POST to `/api/public/scores` (team.py:302). SKILL.md:76, after the lead's edit, describes the endpoints correctly. A README reader will set up an ingestion the code does not use.

Suggested edit: replace the parenthetical in README.md:18 with "self-hosted works too, the events_only mode is enough: traces go through `/api/public/otel/v1/traces`, scores through `/api/public/scores`", in sync with SKILL.md:76.
