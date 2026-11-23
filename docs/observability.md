# Observability

Token usage comes from what the runtime actually provides:

- `usage --notification FILE`: parses the `<usage>` block of a subagent
  completion notification (both the `subagent_tokens: N` and the
  `<subagent_tokens>N</subagent_tokens>` formats), recorded idempotently
  through a dedup key,
- `tokens --add N --model M`: manual entry,
- absent numbers stay absent; the engine never invents values.

`metrics` computes the run set: task success rate, verification pass and
fail rates, average rework rounds, average task duration, restarts and
recovery rate, tokens per task, cost per task and per successful task,
wall time versus the sequential estimate, and the speedup number
(multi-agent vs single-agent).

`report --out FILE` writes a self-contained HTML report from the engine
state (DAG, statuses, attempts, blocks, conflicts, tokens, cost): no
Langfuse and no network needed.

Langfuse export (`langfuse`): one trace per run, a span per task and per
verification attempt, a score per verdict, models and cost in the trace
metadata. Idempotent per run through the exports table payload hash;
retries with backoff; re-uploading an unchanged run is a no-op.
