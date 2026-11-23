"""HTML run report generated from the engine state (spec section 10.2).

Self-contained: one file, inline CSS, no network and no Langfuse
dependency. Shows the task DAG, statuses, owners, durations, verification
attempts, blocks, conflicts, token usage and cost.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import events as eventlog
from .observability import collect_metrics, compute_cost
from .storage import Store


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _dag_lines(tasks: list[dict]) -> list[str]:
    lines = []
    for task in tasks:
        deps = ", ".join(task["deps"]) or "-"
        status = _esc(task["status"])
        owner = _esc(task["owner"] or task["assignee"])
        lines.append(
            f'      <tr><td>{_esc(task["id"])}</td>'
            f'<td>{_esc(deps)}</td>'
            f'<td><span class="st st-{status}">{status}</span></td>'
            f'<td>{owner}</td></tr>')
    return lines


def _task_sections(store: Store, team: str, tasks: list[dict]) -> list[str]:
    sections = []
    for task in tasks:
        attempts = store.list_attempts(team, task["id"])
        rows = []
        for attempt in attempts:
            verdict = _esc(attempt["verdict"])
            verifier = _esc(attempt["verifier"] or "")
            feedback = _esc(attempt["feedback"] or "")
            created = _esc(attempt["created_at"])
            rows.append(
                f'        <tr><td>#{attempt["attempt"]}</td>'
                f'<td>{verdict}</td>'
                f'<td>{verifier}</td>'
                f'<td class="fb">{feedback}</td>'
                f'<td>{created}</td></tr>')
        violations = task.get("metadata", {}).get("workspace_violations", [])
        usage = store.list_usage(team, task_id=task["id"])
        tokens = sum(u.get("total_tokens") or 0 for u in usage)
        cost = sum(filter(None, (compute_cost(u)[0] for u in usage)))
        attempts_body = "".join(rows) if rows else \
            '<tr><td colspan="5">no verification attempts yet</td></tr>'
        violations_html = ""
        if violations:
            violations_html = ('<p class="violations">write-set violations: '
                               + _esc(", ".join(violations)) + "</p>")
        task_id = _esc(task["id"])
        title = _esc(task["title"])
        status = _esc(task["status"])
        owner = _esc(task["owner"] or "-")
        assignee = _esc(task["assignee"])
        writes = _esc(", ".join(task["files_write"]) or "-")
        reads = _esc(", ".join(task["files_read"]) or "-")
        sections.append(f"""
    <section class="card">
      <h2>{task_id}: {title}</h2>
      <p class="meta">status <b class="st st-{status}">
        {status}</b>, owner {owner}
        (assignee {assignee}),
        attempts {task['attempt']}, failed rounds
        {task['verification_failures']}, tokens {tokens},
        cost ${cost:.4f}</p>
      <p class="meta">writes: {writes}
        | reads: {reads}</p>
      {violations_html}
      <table>
        <tr><th>attempt</th><th>verdict</th><th>verifier</th>
            <th>feedback</th><th>at</th></tr>
        {attempts_body}
      </table>
    </section>""")
    return sections


def render_report(store: Store, team: str) -> str:
    team_row = store.get_team(team)
    tasks = store.list_tasks(team)
    run = None
    runs = store.all("SELECT * FROM runs WHERE team_id = ? ORDER BY number "
                     "DESC LIMIT 1", (team,))
    if runs:
        run = runs[0]
    metrics = collect_metrics(store, team, run_id=run["id"] if run else None)
    conflicts = eventlog.list_events(store.conn, team,
                                     event_type="workspace_conflict")
    none_row = '<tr><td colspan="4">none</td></tr>'
    dag_rows = ''.join(_dag_lines(tasks))
    task_cards = ''.join(_task_sections(store, team, tasks))
    blocks = eventlog.list_events(store.conn, team, event_type="task_blocked")
    unread = store.list_messages(team, unread_only=True)

    metric_rows = "".join(
        f'      <tr><td>{_esc(key.replace("_", " "))}</td>'
        f'<td>{_esc(value)}</td></tr>'
        for key, value in metrics.items())
    block_rows = "".join(
        f'  <tr><td>block</td><td>{_esc(e["task_id"])}</td>'
        f'<td>{_esc(e["metadata"].get("reason", ""))}</td>'
        f'<td>{_esc(e["timestamp"])}</td></tr>' for e in blocks)
    conflict_rows = "".join(
        f'  <tr><td>conflict</td><td>{_esc(e["task_id"] or "-")}</td>'
        f'<td>{_esc(e["metadata"])}</td>'
        f'<td>{_esc(e["timestamp"])}</td></tr>' for e in conflicts)
    none_row = '<tr><td colspan="4">none</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>agent-teams report: {_esc(team)}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem;
         color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.05rem; margin: 0 0 .3rem; }}
  .card {{ background: #fff; border: 1px solid #e2e2e2; border-radius: 8px;
           padding: 1rem 1.2rem; margin: 1rem 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
  th, td {{ text-align: left; padding: .3rem .5rem;
            border-bottom: 1px solid #eee; vertical-align: top; }}
  th {{ color: #666; font-weight: 600; }}
  .meta {{ color: #444; font-size: .85rem; margin: .2rem 0; }}
  .st {{ padding: .05rem .45rem; border-radius: 10px; font-size: .78rem; }}
  .st-completed {{ background: #d8f3dc; }}
  .st-in_progress, .st-awaiting_verification {{ background: #fff3cd; }}
  .st-blocked {{ background: #f8d7da; }}
  .st-pending {{ background: #e9ecef; }}
  .st-cancelled {{ background: #dee2e6; color: #777; }}
  .violations {{ color: #a4161a; font-size: .85rem; font-weight: 600; }}
  .fb {{ max-width: 34rem; }}
  footer {{ color: #888; font-size: .78rem; margin-top: 2rem; }}
</style>
</head>
<body>
<h1>agent-teams report: {_esc(team)}</h1>
<p class="meta">created {_esc(team_row['created_at'])}
   | run #{_esc(run['number'] if run else '-')}
   {_esc(run['status'] if run else '')}
   | project {_esc(team_row['project_root'])}</p>

<div class="card">
<h2>Task graph</h2>
<table>
  <tr><th>task</th><th>depends on</th><th>status</th><th>current owner</th></tr>
  {dag_rows}
</table>
</div>

<div class="card">
<h2>Metrics and cost</h2>
<table>
{metric_rows}
</table>
</div>

{task_cards}

<div class="card">
<h2>Blocks and conflicts</h2>
<table>
  <tr><th>kind</th><th>task</th><th>detail</th><th>at</th></tr>
  {block_rows}
  {conflict_rows}
  {none_row if not blocks and not conflicts else ""}
</table>
<p class="meta">{len(unread)} unread mailbox messages</p>
</div>

<footer>generated by agent-teams v1.0.0 from the engine state, no Langfuse
required</footer>
</body>
</html>
"""


def write_report(store: Store, team: str, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(store, team), encoding="utf-8")
    return out_path
