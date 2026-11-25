"""Command-line interface of the v0.3 engine (spec section 10.1, core set).

Every command exits 0 on success and with the error class code on failure
(see errors.py). ``--json`` prints the result of any command as JSON.

The output is pure ASCII in every mode, JSON included: a non-ASCII
character written into a pipe on Windows with the system code page
raises and kills the command (findings-robust R-11).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import dashboard_server, engine
from .errors import EngineError, UsageError


def _add_common(sp, *, team=True):
    sp.add_argument("--project-root", default=".")
    if team:
        sp.add_argument("--team", required=True)


def _print_result(result, *, json_mode=False) -> None:
    if json_mode:
        print(json.dumps(result, indent=2))
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
        if any(isinstance(v, (dict, list)) for v in result.values()):
            # A result that carries a diff, a cost breakdown or a graph is
            # a document, not a status line. It is printed whole: a text
            # line with the nested part left out would read as success
            # and show nothing.
            print(json.dumps(result, indent=2))
            return
        print("OK " + " ".join(f"{k}={v}" for k, v in result.items()))
    else:
        print(json.dumps(result, indent=2))


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
    json_flag = argparse.ArgumentParser(add_help=False)
    json_flag.add_argument("--json", action="store_true",
                           help="print the result as JSON")

    sp = sub.add_parser("init", parents=[json_flag], help="create a team from a tasks file")
    sp.add_argument("--team", required=True)
    sp.add_argument("--tasks", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=_cmd_init)

    sp = sub.add_parser("claim", parents=[json_flag], help="executor claims a pending task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.set_defaults(fn=_cmd_claim)

    sp = sub.add_parser("submit", parents=[json_flag], help="executor sends the task to the gate")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--token", required=True,
                    help="execution token printed by claim")
    sp.set_defaults(fn=_cmd_submit)

    sp = sub.add_parser("verify", parents=[json_flag], help="lead applies the gate verdict")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--verdict", choices=["pass", "fail"], required=True)
    sp.add_argument("--feedback", default="")
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--by", default="verifier",
                    help="recorded actor name, not an authority")
    sp.add_argument("--report", default="",
                    help="path to the verifier's JSON report")
    sp.add_argument("--override-reason", default="",
                    help="lead override: accept without a report, with an "
                         "audited reason")
    sp.set_defaults(fn=_cmd_verify)

    sp = sub.add_parser("release", parents=[json_flag], help="lead returns a blocked task to pending")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--reason", default="")
    sp.set_defaults(fn=_cmd_release)

    sp = sub.add_parser("cancel", parents=[json_flag], help="lead cancels a pending task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--reason", default="")
    sp.set_defaults(fn=_cmd_cancel)

    sp = sub.add_parser("recover", parents=[json_flag], help="requeue tasks whose lease expired")
    _add_common(sp)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--task", default="")
    sp.set_defaults(fn=_cmd_recover)

    sp = sub.add_parser("heartbeat", parents=[json_flag], help="executor refreshes its lease")
    _add_common(sp)
    sp.add_argument("--by", required=True)
    sp.add_argument("--token", required=True)
    sp.set_defaults(fn=_cmd_heartbeat)

    sp = sub.add_parser("tokens", parents=[json_flag], help="record token usage for a task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--add", type=int, default=0)
    sp.add_argument("--by", default="lead")
    sp.add_argument("--model", default="")
    sp.add_argument("--dedup-key", default="")
    sp.set_defaults(fn=_cmd_tokens)

    sp = sub.add_parser("message", parents=[json_flag], help="send a mailbox message")
    _add_common(sp)
    sp.add_argument("--to", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--text", required=True)
    sp.set_defaults(fn=_cmd_message)

    sp = sub.add_parser("read", parents=[json_flag], help="read your unread messages")
    _add_common(sp)
    sp.add_argument("--for", dest="for_", required=True)
    sp.set_defaults(fn=_cmd_read)

    sp = sub.add_parser("status", parents=[json_flag], help="task statuses and unread counters")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_status)

    sp = sub.add_parser("inspect", parents=[json_flag], help="one task with attempts and events")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.set_defaults(fn=_cmd_inspect)

    sp = sub.add_parser("integrate", parents=[json_flag], help="merge accepted task branches, "
                                         "run the integration gate")
    _add_common(sp)
    sp.add_argument("--lead-token", required=True)
    sp.add_argument("--check", default="",
                    help="shell command to run in the merged tree")
    sp.add_argument("--apply", action="store_true",
                    help="merge the integration branch into the project branch")
    sp.set_defaults(fn=_cmd_integrate)

    sp = sub.add_parser("metrics", parents=[json_flag], help="run metrics and cost")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_metrics)

    sp = sub.add_parser("usage", parents=[json_flag], help="record usage from a notification file")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.add_argument("--agent", required=True)
    sp.add_argument("--notification", required=True)
    sp.set_defaults(fn=_cmd_usage)

    sp = sub.add_parser("plan", parents=[json_flag], help="validate a spec and show the DAG")
    sp.add_argument("--tasks", required=True)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(fn=_cmd_plan)

    sp = sub.add_parser("graph", parents=[json_flag], help="ASCII graph of task statuses")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_graph)

    sp = sub.add_parser("agents", parents=[json_flag], help="agents with roles and states")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_agents)

    sp = sub.add_parser("logs", parents=[json_flag], help="recent events")
    _add_common(sp)
    sp.add_argument("--task", default="")
    sp.add_argument("--limit", type=int, default=40)
    sp.set_defaults(fn=_cmd_logs)

    sp = sub.add_parser("diff", parents=[json_flag], help="changed files of a submitted task")
    _add_common(sp)
    sp.add_argument("--task", required=True)
    sp.set_defaults(fn=_cmd_diff)

    sp = sub.add_parser("cost", parents=[json_flag], help="cost breakdown per task")
    _add_common(sp)
    sp.set_defaults(fn=_cmd_cost)

    sp = sub.add_parser("workspace", parents=[json_flag], help="list worktrees or clean one")
    _add_common(sp)
    sp.add_argument("--clean", default="", help="task id to clean")
    sp.set_defaults(fn=_cmd_workspace)

    sp = sub.add_parser("report", parents=[json_flag], help="write the HTML run report")
    _add_common(sp)
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=_cmd_report)

    sp = sub.add_parser("apply", parents=[json_flag], help="apply the validated integration "
                                      "branch to the project (fast-forward)")
    _add_common(sp)
    sp.add_argument("--lead-token", required=True)
    sp.set_defaults(fn=_cmd_apply)

    sp = sub.add_parser("langfuse", parents=[json_flag], help="export the run to Langfuse")
    _add_common(sp)
    sp.add_argument("--name", default="")
    sp.set_defaults(fn=_cmd_langfuse)

    sp = sub.add_parser("budget", parents=[json_flag],
                        help="show the budget and the spend, or change the budget as the lead")
    _add_common(sp)
    sp.add_argument("--lead-token", default="",
                    help="with it the given limits are set (an audited decision)")
    sp.add_argument("--usd", default=None,
                    help="the budget of the team in USD, or 'none' for no limit")
    sp.add_argument("--per-task", dest="per_task", default=None,
                    help="the limit of one task in USD, or 'none' for no limit")
    sp.add_argument("--warn-ratio", dest="warn_ratio", default=None,
                    help="the share of the budget from which the board warns (0 to 1)")
    sp.set_defaults(fn=_cmd_budget)

    sp = sub.add_parser("ui", parents=[json_flag],
                        help="open the read-only web dashboard of a team")
    _add_common(sp)
    sp.add_argument("--host", default=dashboard_server.DEFAULT_HOST,
                    help="address to bind (default: this machine only)")
    sp.add_argument("--port", type=int, default=dashboard_server.DEFAULT_PORT)
    sp.add_argument("--open", action="store_true",
                    help="open the dashboard in the default browser")
    sp.add_argument("--lead-token", dest="lead_token", default="",
                    help="lets the owner act from the board (v2.1 spec 17); the "
                         "token stays in the memory of this process. Use "
                         "--lead-token-file where other processes may read the "
                         "command line")
    sp.add_argument("--lead-token-file", dest="lead_token_file", default="",
                    help="read the lead token from this file instead of the "
                         "command line")
    sp.set_defaults(fn=_cmd_ui)

    for name, fn, what in (
            ("pause", _cmd_pause, "stop new tasks from starting; work in progress goes on"),
            ("resume", _cmd_resume, "let new tasks start again after a pause")):
        sp = sub.add_parser(name, parents=[json_flag], help=what)
        _add_common(sp)
        sp.add_argument("--lead-token", default="")
        if name == "pause":
            sp.add_argument("--reason", default="")
        sp.set_defaults(fn=fn)

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
                            by=args.by, report=report,
                            override_reason=args.override_reason)


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


def _limit(text, what: str):
    """A money limit from the command line: a number, 'none', or absent."""
    if text is None:
        return engine.KEEP
    if text.strip().lower() in ("none", "off"):
        return None
    try:
        return float(text)
    except ValueError:
        raise UsageError(f"{what} must be a number or 'none', got {text!r}") from None


def _cmd_budget(args):
    result = engine.op_budget(Path(args.project_root), args.team, args.lead_token or None,
                              usd=_limit(args.usd, "--usd"),
                              per_task=_limit(args.per_task, "--per-task"),
                              warn_ratio=_limit(args.warn_ratio, "--warn-ratio"))
    if args.json:
        return result
    money = lambda value: "not set" if value is None else engine._usd(value)   # noqa: E731
    spent = engine._usd(result["spent_usd"])
    print(f"team {result['team']}: budget {money(result['budget_usd'])}, spent {spent},"
          f" left {money(result['remaining_usd'])}")
    warn = result["budget_warn_ratio"]
    print(f"warning from {warn:.0%} of the budget; limit of one task "
          f"{money(result['budget_per_task_usd'])}")
    if result["unknown_records"]:
        print(f"note: {result['unknown_records']} usage records carry no price; "
              "the limit is watched over the known spend only")
    if result["state"] == "limit":      # the engine decides where the team stands
        print("LIMIT REACHED: no new attempt starts until the lead raises the budget "
              "(--usd <new limit> --lead-token <tok>); running executors are not stopped")
    elif result["state"] == "warning":
        print("warning: the budget is nearly spent")
    for task_id in result["tasks_at_limit"]:
        print(f"task {task_id}: its limit is spent ({engine._usd(result['tasks'][task_id])}"
              f" of {engine._usd(result['budget_per_task_usd'])})")
    return None


def _cmd_ui(args):
    token = args.lead_token
    if args.lead_token_file:
        if token:
            raise UsageError("give the lead token once: --lead-token or --lead-token-file")
        try:
            token = Path(args.lead_token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise UsageError(f"cannot read --lead-token-file: {exc}") from exc
    return dashboard_server.op_ui(Path(args.project_root), args.team,
                                  host=args.host, port=args.port,
                                  open_browser=args.open, json_mode=args.json,
                                  out=sys.stdout, lead_token=token or None)


def _cmd_pause(args):
    result = engine.op_pause(Path(args.project_root), args.team, args.lead_token,
                             args.reason)
    if not args.json:
        if result["already"]:
            print(f"OK team {args.team} is paused already")
            return
    return result


def _cmd_resume(args):
    result = engine.op_resume(Path(args.project_root), args.team, args.lead_token)
    if not args.json:
        if result["already"]:
            print(f"OK team {args.team} is not paused")
            return
    return result


def _known_options(parser: argparse.ArgumentParser) -> set[str]:
    found = set(parser._option_string_actions)
    for action in parser._actions:
        if isinstance(getattr(action, "choices", None), dict):   # the subcommands
            for sub in action.choices.values():
                found |= _known_options(sub)
    return found


def carry_values(argv: list[str], options: set[str]) -> list[str]:
    """Join a token that starts with a dash to its option with ``=``.

    A lead token of a team created before 2.1.0a3 may start with a dash.
    On a command line such a value reads as the next option and the
    command refuses its own token (``expected one argument``);
    ``--lead-token=-abc`` is unambiguous. A real option that follows a
    token left out is left alone, so its usage error stays.
    """
    out: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        following = argv[index + 1] if index + 1 < len(argv) else None
        if item in ("--lead-token", "--token") and following is not None \
                and following.startswith("-") and following != "-" and following not in options:
            out.append(item + "=" + following)
            index += 2
            continue
        out.append(item)
        index += 1
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(carry_values(argv, _known_options(parser)))
    json_mode = getattr(args, "json", False)
    try:
        result = args.fn(args)
    except EngineError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(json.dumps(exc.detail), file=sys.stderr)
        return exc.code
    except BrokenPipeError:
        return 0
    if result is None:
        return 0
    _print_result(result, json_mode=json_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
