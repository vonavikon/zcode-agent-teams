"""Command-line interface of the v0.3 engine (spec section 10.1, core set).

Every command exits 0 on success and with the error class code on failure
(see errors.py). ``--json`` switches status/read/recover output to JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import engine
from .errors import EngineError


def _add_common(sp, *, team=True):
    sp.add_argument("--project-root", default=".")
    if team:
        sp.add_argument("--team", required=True)


def _print_result(result, *, json_mode=False) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if isinstance(result, dict):
        if "lead_token" in result:
            print(f"OK team {result['team']}: {result['tasks']} tasks, {result['dir']}")
            for warning in result.get("warnings", []):
                print(f"warning: {warning}")
            print(f"lead token (shown once, keep it in the lead session): "
                  f"{result['lead_token']}")
            return
        if "execution_token" in result:
            print(f"OK claim {result['task']} -> {result['owner']} "
                  f"(attempt {result['attempt']})")
            print(f"execution token (pass it to the executor, shown once): "
                  f"{result['execution_token']}")
            if result.get("workspace"):
                print(f"workspace={result['workspace']}")
            return
        print("OK " + " ".join(f"{k}={v}" for k, v in result.items()
                               if not isinstance(v, (dict, list))))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _status_text(status: dict) -> None:
    run = status["run"]
    run_txt = f"run #{run['number']} ({run['status']})" if run else "no run"
    print(f"team {status['team']} (created {status['created']}), {run_txt}")
    for t in status["tasks"]:
        print(f"  {t['id']:<24} {t['status']:<24} failed rounds "
              f"{t['failed_rounds']}  tokens {t['tokens']:<7} "
              f"{(t['owner'] or t['assignee']):<16} {t['title']}")
    for agent, count in sorted(status["unread"].items()):
        print(f"  mailbox {agent}: {count} unread")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ateam.py",
        description="agent-teams engine v0.3: shared state in SQLite, "
                    "acceptance gate, lead and execution tokens")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create a team from a tasks file")
    sp.add_argument("--team", required=True)
    sp.add_argument("--tasks", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=_cmd_init)

    sp = sub.add_parser("claim", help="executor claims a pending task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.set_defaults(fn=_cmd_claim)

    sp = sub.add_parser("submit", help="executor sends the task to the gate")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--token", required=True,
                    help="execution token printed by claim")
    sp.set_defaults(fn=_cmd_submit)

    sp = sub.add_parser("verify", help="lead applies the gate verdict")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--verdict", choices=["pass", "fail"], required=True)
    sp.add_argument("--feedback", default="")
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--by", default="verifier",
                    help="recorded actor name, not an authority")
    sp.add_argument("--report", default="",
                    help="path to the verifier's JSON report (optional)")
    sp.set_defaults(fn=_cmd_verify)

    sp = sub.add_parser("release", help="lead returns a blocked task to pending")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--reason", default="")
    sp.set_defaults(fn=_cmd_release)

    sp = sub.add_parser("cancel", help="lead cancels a pending task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--reason", default="")
    sp.set_defaults(fn=_cmd_cancel)

    sp = sub.add_parser("recover", help="requeue tasks whose lease expired")
    _add_common(sp)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--task", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=_cmd_recover)

    sp = sub.add_parser("heartbeat", help="executor refreshes its lease")
    _add_common(sp)
    sp.add_argument("--by", required=True)
    sp.add_argument("--token", required=True)
    sp.set_defaults(fn=_cmd_heartbeat)

    sp = sub.add_parser("tokens", help="record token usage for a task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--add", type=int, default=0)
    sp.add_argument("--by", default="lead")
    sp.add_argument("--model", default="")
    sp.add_argument("--dedup-key", default="")
    sp.set_defaults(fn=_cmd_tokens)

    sp = sub.add_parser("message", help="send a mailbox message")
    _add_common(sp)
    sp.add_argument("--to", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--text", required=True)
    sp.set_defaults(fn=_cmd_message)

    sp = sub.add_parser("read", help="read your unread messages")
    _add_common(sp)
    sp.add_argument("--for", dest="for_", required=True)
    sp.set_defaults(fn=_cmd_read)

    sp = sub.add_parser("status", help="task statuses and unread counters")
    _add_common(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=_cmd_status)

    sp = sub.add_parser("inspect", help="one task with attempts and events")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--json", action="store_true", default=True)
    sp.set_defaults(fn=_cmd_inspect)

    sp = sub.add_parser("integrate", help="merge accepted task branches, "
                                         "run the integration gate")
    _add_common(sp)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--check", default="",
                    help="shell command to run in the merged tree")
    sp.add_argument("--apply", action="store_true",
                    help="merge the integration branch into the project branch")
    sp.set_defaults(fn=_cmd_integrate)

    sp = sub.add_parser("metrics", help="run metrics and cost")
    _add_common(sp)
    sp.add_argument("--json", action="store_true", default=True)
    sp.set_defaults(fn=_cmd_metrics)

    sp = sub.add_parser("usage", help="record usage from a notification file")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--agent", required=True)
    sp.add_argument("--notification", required=True)
    sp.set_defaults(fn=_cmd_usage)

    sp = sub.add_parser("plan", help="validate a spec and show the DAG")
    sp.add_argument("--tasks", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=_cmd_plan)

    sp = sub.add_parser("graph", help="ASCII graph of task statuses")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_graph)

    sp = sub.add_parser("agents", help="agents with roles and states")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_agents)

    sp = sub.add_parser("logs", help="recent events")
    _add_common(sp)
    sp.add_argument("--task", default="")
    sp.add_argument("--limit", type=int, default=40)
    sp.set_defaults(fn=_cmd_logs)

    sp = sub.add_parser("diff", help="changed files of a submitted task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.set_defaults(fn=_cmd_diff)

    sp = sub.add_parser("cost", help="cost breakdown per task")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_cost)

    sp = sub.add_parser("workspace", help="list worktrees or clean one")
    _add_common(sp)
    sp.add_argument("--clean", default="", help="task id to clean")
    sp.set_defaults(fn=_cmd_workspace)

    sp = sub.add_parser("report", help="write the HTML run report")
    _add_common(sp)
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=_cmd_report)

    sp = sub.add_parser("apply", help="apply the validated integration "
                                      "branch to the project (fast-forward)")
    _add_common(sp)
    sp.add_argument("--lead-token", required=True)
    sp.set_defaults(fn=_cmd_apply)

    sp = sub.add_parser("langfuse", help="export the run to Langfuse")
    _add_common(sp)
    sp.add_argument("--name", default="")
    sp.set_defaults(fn=_cmd_langfuse)

    return p


def _cmd_init(args):
    return engine.op_init(Path(args.project_root), args.team,
                          Path(args.tasks))


def _cmd_claim(args):
    return engine.op_claim(Path(args.project_root), args.team, args.task,
                           args.by)


def _cmd_submit(args):
    return engine.op_submit(Path(args.project_root), args.team, args.task,
                            args.by, args.token)


def _cmd_verify(args):
    report = None
    if args.report:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    return engine.op_verify(Path(args.project_root), args.team, args.task,
                            args.verdict, args.feedback, args.lead_token,
                            by=args.by, report=report)


def _cmd_release(args):
    return engine.op_release(Path(args.project_root), args.team, args.task,
                             args.lead_token, args.reason)


def _cmd_cancel(args):
    return engine.op_cancel(Path(args.project_root), args.team, args.task,
                            args.lead_token, args.reason)


def _cmd_recover(args):
    return engine.op_recover(Path(args.project_root), args.team,
                             args.lead_token, args.task or None)


def _cmd_heartbeat(args):
    return engine.op_heartbeat(Path(args.project_root), args.team, args.by,
                               args.token)


def _cmd_tokens(args):
    return engine.op_tokens(Path(args.project_root), args.team, args.task,
                            args.add, args.by, args.model or None,
                            "manual", args.dedup_key or None)


def _cmd_message(args):
    return engine.op_message(Path(args.project_root), args.team, args.to,
                             args.by, args.text)


def _cmd_read(args):
    return engine.op_read(Path(args.project_root), args.team, args.for_)


def _cmd_status(args):
    status = engine.op_status(Path(args.project_root), args.team)
    if args.json:
        return status
    _status_text(status)
    return None


def _cmd_inspect(args):
    return engine.op_inspect(Path(args.project_root), args.team, args.task)


def _cmd_integrate(args):
    return engine.op_integrate(Path(args.project_root), args.team,
                               args.lead_token, args.check, apply=args.apply)


def _cmd_metrics(args):
    return engine.op_metrics(Path(args.project_root), args.team)


def _cmd_usage(args):
    return engine.op_usage(Path(args.project_root), args.team, args.task,
                           args.agent, Path(args.notification))


def _cmd_plan(args):
    return engine.op_plan(Path(args.project_root), Path(args.tasks))


def _cmd_graph(args):
    return engine.op_graph(Path(args.project_root), args.team)


def _cmd_agents(args):
    return engine.op_agents(Path(args.project_root), args.team)


def _cmd_logs(args):
    return engine.op_logs(Path(args.project_root), args.team, args.limit,
                          args.task or None)


def _cmd_diff(args):
    return engine.op_diff(Path(args.project_root), args.team, args.task)


def _cmd_cost(args):
    return engine.op_cost(Path(args.project_root), args.team)


def _cmd_workspace(args):
    return engine.op_workspace(Path(args.project_root), args.team,
                               args.clean or None)


def _cmd_report(args):
    return engine.op_report(Path(args.project_root), args.team,
                            Path(args.out))


def _cmd_apply(args):
    return engine.op_apply(Path(args.project_root), args.team,
                           args.lead_token)


def _cmd_langfuse(args):
    from .langfuse import op_langfuse

    return op_langfuse(Path(args.project_root), args.team, args.name)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_mode = getattr(args, "json", False)
    try:
        result = args.fn(args)
    except EngineError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(json.dumps(exc.detail, ensure_ascii=False), file=sys.stderr)
        return exc.code
    except BrokenPipeError:
        return 0
    if result is None:
        return 0
    _print_result(result, json_mode=json_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
