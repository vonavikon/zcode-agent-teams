"""Command-line output (spec section 10.1): nothing is lost.

In text mode a result that carried a diff, a cost breakdown or a graph
used to print as ``OK task=a``: the nested part was left out, the line
read as success, and an agent saw nothing. A nested result is a
document and is printed whole. ``--json`` is accepted by every command.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agent_teams import cli


def subcommands() -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in cli.build_parser()._actions
                  if isinstance(a, argparse._SubParsersAction))
    return action.choices


class TestTextMode:
    def test_a_flat_result_is_one_status_line(self, capsys):
        cli._print_result({"task": "a", "status": "completed"})
        assert capsys.readouterr().out == "OK task=a status=completed\n"

    def test_a_nested_result_is_printed_whole(self, capsys):
        for result in (
            {"task": "a", "diff": {"files": ["out/a.md"], "added": 3}},
            {"team": "t", "cost": [{"task": "a", "cost_usd": 0.5}]},
            {"team": "t", "graph": ["a -> b"], "summary": {"done": 1}},
            {"task": "a", "status": "completed", "events": []},  # an empty list is data too
        ):
            cli._print_result(result)
            assert json.loads(capsys.readouterr().out) == result

    def test_a_list_is_a_document(self, capsys):
        result = [{"task": "a"}, {"task": "b"}]
        cli._print_result(result)
        assert json.loads(capsys.readouterr().out) == result


class TestOutputIsAsciiEverywhere:
    # A non-ASCII character written into a pipe on Windows with the system
    # code page raises and kills the command (findings-robust R-11).
    CYRILLIC = {"task": "a", "title": "Собрать отчёт", "files": ["отчёт.md"],
                "nested": {"note": "проверено"}}

    def test_text_and_json_output_are_pure_ascii(self, capsys):
        for json_mode in (False, True):
            cli._print_result(self.CYRILLIC, json_mode=json_mode)
            out = capsys.readouterr().out
            assert out.isascii(), (json_mode, out)
            assert json.loads(out) == self.CYRILLIC

    def test_the_error_detail_is_ascii_too(self, monkeypatch, capsys):
        from agent_teams.errors import UsageError

        def fail(_args):
            raise UsageError("bad spec", detail={"title": "Собрать отчёт"})

        monkeypatch.setattr(cli, "_cmd_plan", fail)
        cli.main(["plan", "--tasks", "x.json"])
        err = capsys.readouterr().err
        assert err.isascii() and "Собрать отчёт" not in err
        assert "Собрать" in json.loads(err.splitlines()[-1])["title"]


class TestOneTimeTokensStayReadable:
    def test_init_prints_the_lead_token_once_with_its_warning(self, capsys):
        result = {"team": "t", "tasks": 2, "dir": "/x/.agent-teams/t",
                  "lead_token": "SECRET-LEAD", "warnings": ["no git repository found"],
                  "run_base": None}
        cli._print_result(result)
        out = capsys.readouterr().out.splitlines()
        assert out == ["OK team t: 2 tasks, /x/.agent-teams/t",
                       "warning: no git repository found",
                       "lead token (shown once, keep it in the lead session): SECRET-LEAD"]

    def test_claim_prints_the_execution_token_once(self, capsys):
        cli._print_result({"task": "a", "owner": "w1", "attempt": 1,
                           "execution_token": "SECRET-EXEC", "workspace": "/ws"})
        out = capsys.readouterr().out.splitlines()
        assert out == ["OK claim a -> w1 (attempt 1)",
                       "execution token (pass it to the executor, shown once): SECRET-EXEC",
                       "workspace=/ws"]


class TestJsonEverywhere:
    def test_every_command_accepts_json(self):
        for name, parser in subcommands().items():
            assert "--json" in parser._option_string_actions, name

    def test_json_mode_prints_the_whole_result_including_nested_data(self, capsys):
        result = {"task": "a", "diff": {"files": ["out/a.md"]}}
        cli._print_result(result, json_mode=True)
        assert json.loads(capsys.readouterr().out) == result

    def test_text_is_the_default_for_every_command(self):
        # The flag is one shared object: a default flipped for one command
        # silently flips it for all of them (found on 2026-11-25).
        for name, parser in subcommands().items():
            assert parser._option_string_actions["--json"].default is False, name
        parser = cli.build_parser()
        assert parser.parse_args(["inspect", "--team", "t", "--task", "a"]).json is False
        assert parser.parse_args(["metrics", "--team", "t"]).json is False

    def test_inspect_and_metrics_print_the_whole_document_in_text_mode(
            self, monkeypatch, capsys):
        nested = {"team": "t", "tasks": [{"id": "a"}], "summary": {"done": 1}}
        monkeypatch.setattr(cli.engine, "op_inspect", lambda *_args, **_kw: nested)
        monkeypatch.setattr(cli.engine, "op_metrics", lambda *_args, **_kw: nested)
        for argv in (["inspect", "--team", "t", "--task", "a"], ["metrics", "--team", "t"]):
            capsys.readouterr()
            assert cli.main(argv) == 0
            assert json.loads(capsys.readouterr().out) == nested

    def test_json_on_init_keeps_the_token_in_the_document(self, capsys):
        cli._print_result({"team": "t", "lead_token": "X", "tasks": 1}, json_mode=True)
        assert json.loads(capsys.readouterr().out)["lead_token"] == "X"


class TestUiCommand:
    def test_defaults_are_this_machine_and_the_standard_port(self):
        args = cli.build_parser().parse_args(["ui", "--team", "t"])
        assert (args.host, args.port, args.open) == ("127.0.0.1", 8765, False)

    def test_the_command_runs_the_dashboard_and_hands_over_its_options(self, monkeypatch, capsys):
        seen = {}
        monkeypatch.setattr(cli.dashboard_server, "op_ui",
                            lambda root, team, **options: seen.update(
                                root=str(root), team=team, **options) or {"url": "http://x"})
        code = cli.main(["ui", "--team", "t", "--project-root", "/p", "--host",
                         "127.0.0.1", "--port", "9000", "--open", "--json"])
        assert code == 0
        assert seen["team"] == "t" and Path(seen["root"]) == Path("/p")
        assert (seen["host"], seen["port"], seen["open_browser"], seen["json_mode"]) == \
            ("127.0.0.1", 9000, True, True)
        assert json.loads(capsys.readouterr().out) == {"url": "http://x"}

    def test_a_taken_port_is_an_error_with_its_code_not_a_traceback(self, monkeypatch, capsys):
        from agent_teams.errors import UsageError

        def refuse(*_args, **_options):
            raise UsageError("port 8765 is already in use")

        monkeypatch.setattr(cli.dashboard_server, "op_ui", refuse)
        code = cli.main(["ui", "--team", "t"])
        assert code == UsageError("x").code
        assert "already in use" in capsys.readouterr().err

    def test_the_team_is_required(self):
        with pytest.raises(SystemExit) as stopped:
            cli.build_parser().parse_args(["ui"])
        assert stopped.value.code == 2   # argparse usage error, before anything runs
