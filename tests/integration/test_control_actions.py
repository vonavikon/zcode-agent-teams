"""Control from the board (v2.1 spec 17): the owner asks, the engine does.

The rules under test:

- the lead token lives in the memory of the board's process: it is in
  no response and in no page;
- without it the board stays read-only, and a POST is refused like any
  other write method;
- a request from another origin is refused;
- every action goes through the engine, under its lock and with its
  checks: a pause stops new starts and nothing else; a stage cancelled
  in work is fenced; an action with consequences needs a confirmation.
"""
from __future__ import annotations

import http.client
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_teams import engine
from agent_teams.dashboard_server import serve
from agent_teams.errors import PermissionDenied, StateRejected, UsageError

SCRIPTS = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts"
TEAM = SCRIPTS / "team.py"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def spec_file(root: Path) -> Path:
    spec = {"tasks": [
        {"id": "a", "title": "Собрать отчёт", "assignee": "w1", "files_write": ["out/a.md"],
         "acceptance": ["готово"], "deps": []},
        {"id": "b", "title": "Проверить отчёт", "assignee": "w2", "files_write": ["out/b.md"],
         "acceptance": ["проверено"], "deps": []},
        {"id": "c", "title": "Оценить риски", "assignee": "w1", "files_write": ["out/c.md"],
         "acceptance": ["риски"], "deps": []}]}
    path = root / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def team(tmp_path):
    """A git-backed team: an attempt gets a worktree that can be fenced."""
    (tmp_path / "out").mkdir()
    # The spec and the team's directory are not part of the project: apply
    # refuses a project with uncommitted changes.
    (tmp_path / ".gitignore").write_text(".agent-teams/\nspec.json\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@e.i")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "out" / ".gitkeep").write_text("", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    created = engine.op_init(tmp_path, "t", spec_file(tmp_path))
    return {"root": tmp_path, "lead": created["lead_token"],
            "db": tmp_path / ".agent-teams" / "t" / "state.db"}


def deliver(team: dict, claim: dict, task_id: str, text: str) -> None:
    workspace = Path(claim["workspace"])
    (workspace / "out" / f"{task_id}.md").write_text(text, encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", "work")


def task_row(team: dict, task_id: str) -> dict:
    with sqlite3.connect(f"file:{team['db']}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT status, owner, execution_token_hash, workspace_path,"
                                 " attempt, metadata_json FROM tasks WHERE id = ?",
                                 (task_id,)).fetchone())


def events(team: dict, kind: str) -> list[dict]:
    with sqlite3.connect(f"file:{team['db']}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT metadata_json FROM events WHERE event_type = ? ORDER BY seq",
                            (kind,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def run_row(team: dict) -> dict:
    with sqlite3.connect(f"file:{team['db']}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT status, metadata_json FROM runs ORDER BY number DESC"
                                 " LIMIT 1").fetchone())


class TestPause:
    def test_a_pause_stops_new_starts_and_nothing_else(self, team):
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        result = engine.op_pause(team["root"], "t", team["lead"])
        assert result == {"team": "t", "paused": True, "already": False,
                          "paused_at": result["paused_at"]}
        assert json.loads(run_row(team)["metadata_json"])["paused_at"] == result["paused_at"]
        # A new start is refused before anything is made for it.
        with pytest.raises(StateRejected, match="PAUSED"):
            engine.op_claim(team["root"], "t", "b", "w2")
        assert task_row(team, "b")["status"] == "pending"
        assert not (team["root"] / ".agent-teams" / "t" / "tasks" / "b").exists()
        # The attempt that was already running is not stopped.
        engine.op_heartbeat(team["root"], "t", "w1", claim["execution_token"])
        deliver(team, claim, "a", "отчёт\n")
        engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])
        assert task_row(team, "a")["status"] == "awaiting_verification"
        # The lead resumes, and the new start goes.
        assert engine.op_resume(team["root"], "t", team["lead"]) == {
            "team": "t", "paused": False, "already": False, "paused_at": result["paused_at"]}
        assert "paused_at" not in json.loads(run_row(team)["metadata_json"])
        engine.op_claim(team["root"], "t", "b", "w2")
        assert task_row(team, "b")["status"] == "in_progress"

    def test_it_is_idempotent_and_in_the_journal(self, team):
        first = engine.op_pause(team["root"], "t", team["lead"])
        again = engine.op_pause(team["root"], "t", team["lead"])
        assert again["already"] is True and len(events(team, "run_paused")) == 1
        assert events(team, "run_paused")[0]["reason"] == "paused by the lead"
        engine.op_pause(team["root"], "t", team["lead"], "owner asked")
        assert len(events(team, "run_paused")) == 1      # a paused team is not paused twice
        assert first["paused_at"] == json.loads(run_row(team)["metadata_json"])["paused_at"]
        assert engine.op_resume(team["root"], "t", team["lead"])["already"] is False
        assert engine.op_resume(team["root"], "t", team["lead"]) == {
            "team": "t", "paused": False, "already": True, "paused_at": None}
        assert len(events(team, "run_resumed")) == 1
        assert events(team, "run_resumed")[0]["paused_at"] == first["paused_at"]

    def test_only_the_lead_pauses_and_only_a_team_in_work(self, team):
        with pytest.raises(PermissionDenied):
            engine.op_pause(team["root"], "t", "not-the-token")
        with pytest.raises(PermissionDenied):
            engine.op_resume(team["root"], "t", None)
        # A finished run has nothing to pause.
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        for task_id, by in (("b", "w2"), ("c", "w1")):
            engine.op_cancel(team["root"], "t", task_id, team["lead"])
        deliver(team, claim, "a", "отчёт\n")
        engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])
        engine.op_verify(team["root"], "t", "a", "pass", "", team["lead"], override_reason="demo")
        # The two cancelled stages are not in the way of what was accepted.
        engine.op_integrate(team["root"], "t", team["lead"], apply=True)
        assert run_row(team)["status"] == "finished"
        with pytest.raises(StateRejected, match="nothing to pause"):
            engine.op_pause(team["root"], "t", team["lead"])

    def test_the_command_line_refuses_a_claim_with_exit_code_2(self, team):
        engine.op_pause(team["root"], "t", team["lead"])
        proc = subprocess.run(
            [sys.executable, str(TEAM), "claim", "--team", "t", "--task", "a", "--by", "w1",
             "--project-root", str(team["root"])],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert proc.returncode == 2
        assert "PAUSED" in proc.stderr
        resumed = subprocess.run(
            [sys.executable, str(TEAM), "resume", "--team", "t", "--lead-token", team["lead"],
             "--project-root", str(team["root"]), "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert resumed.returncode == 0 and json.loads(resumed.stdout)["paused"] is False
        plain = subprocess.run(
            [sys.executable, str(TEAM), "resume", "--team", "t", "--lead-token", team["lead"],
             "--project-root", str(team["root"])],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert plain.returncode == 0 and plain.stdout.strip() == "OK team t is not paused"


class TestCancel:
    def test_a_stage_in_work_is_cancelled_and_its_attempt_fenced(self, team):
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        workspace = Path(claim["workspace"])
        assert workspace.exists()
        result = engine.op_cancel(team["root"], "t", "a", team["lead"], "owner asked")
        assert result["status"] == "cancelled" and result["fenced"] is True
        assert Path(result["quarantine"]).exists()
        row = task_row(team, "a")
        assert row["status"] == "cancelled" and row["execution_token_hash"] is None
        assert row["workspace_path"] is None
        assert json.loads(row["metadata_json"])["quarantine_dir"] == result["quarantine"]
        # The attempt is dead: its token submits nothing any more.
        with pytest.raises(Exception, match="token"):
            engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])
        # The specialist is free.
        with sqlite3.connect(team["db"]) as conn:
            agent = conn.execute("SELECT status, current_task FROM agents WHERE id = 'w1'").fetchone()
        assert tuple(agent) == ("idle", None)
        cancelled = events(team, "task_cancelled")[-1]
        assert cancelled["from"] == "in_progress" and cancelled["initiator"] == "lead"
        assert cancelled["reason"] == "owner asked"
        # The attempt's whole workspace was moved aside, as it was: this
        # one had written nothing yet.
        assert list((Path(result["quarantine"]) / "workspace").iterdir())        # moved, not deleted
        assert not (Path(result["quarantine"]) / "workspace" / "out" / "a.md").exists()
        assert not workspace.exists()

    def test_a_stage_on_check_can_be_cancelled_too(self, team):
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        deliver(team, claim, "a", "отчёт\n")
        engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])
        result = engine.op_cancel(team["root"], "t", "a", team["lead"])
        assert result["fenced"] is True
        # What the attempt wrote is kept for salvage, and only there.
        kept = Path(result["quarantine"]) / "workspace" / "out" / "a.md"
        assert kept.read_text(encoding="utf-8") == "отчёт\n"

    def test_a_pending_stage_is_closed_as_before(self, team):
        result = engine.op_cancel(team["root"], "t", "b", team["lead"])
        assert result == {"task": "b", "status": "cancelled", "fenced": False, "quarantine": None}
        assert task_row(team, "b")["status"] == "cancelled"

    def test_only_the_lead_cancels_and_never_a_closed_stage(self, team):
        with pytest.raises(PermissionDenied):
            engine.op_cancel(team["root"], "t", "b", "")
        engine.op_cancel(team["root"], "t", "b", team["lead"])
        with pytest.raises(StateRejected):
            engine.op_cancel(team["root"], "t", "b", team["lead"])
        with pytest.raises(Exception, match="not found"):
            engine.op_cancel(team["root"], "t", "nobody", team["lead"])


class TestOneDoorForTheBoard:
    def test_an_action_with_consequences_needs_a_confirmation(self, team):
        with pytest.raises(UsageError, match="needs a confirmation"):
            engine.op_action(team["root"], "t", "pause", team["lead"])
        with pytest.raises(UsageError, match="needs a confirmation"):
            engine.op_action(team["root"], "t", "cancel", team["lead"], task_id="b")
        assert run_row(team)["metadata_json"] in ("{}", None)
        done = engine.op_action(team["root"], "t", "pause", team["lead"], confirm=True)
        assert done["action"] == "pause" and done["paused"] is True
        engine.op_action(team["root"], "t", "resume", team["lead"])
        assert json.loads(run_row(team)["metadata_json"]) == {}

    def test_what_is_refused_before_the_engine_is_asked(self, team):
        with pytest.raises(UsageError, match="unknown action"):
            engine.op_action(team["root"], "t", "explode", team["lead"], confirm=True)
        with pytest.raises(UsageError, match="needs a task"):
            engine.op_action(team["root"], "t", "retry", team["lead"])
        with pytest.raises(UsageError, match="needs usd"):
            engine.op_action(team["root"], "t", "budget", team["lead"], confirm=True)
        with pytest.raises(PermissionDenied):
            engine.op_action(team["root"], "t", "pause", "wrong", confirm=True)
        assert set(engine.DANGEROUS_ACTIONS) <= set(engine.ACTIONS)

    def test_the_board_runs_no_agent_it_asks_the_lead(self, team):
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        deliver(team, claim, "a", "отчёт\n")
        engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])
        engine.op_action(team["root"], "t", "request_check", team["lead"], task_id="a")
        inbox = engine.op_read(team["root"], "t", "lead")
        assert len(inbox) == 1 and inbox[0]["from"] == "owner"
        assert "verify task a" in inbox[0]["text"]
        assert task_row(team, "a")["status"] == "awaiting_verification"

    def test_retry_and_budget_go_through_their_own_operations(self, team):
        engine.op_cancel(team["root"], "t", "c", team["lead"])
        claim = engine.op_claim(team["root"], "t", "a", "w1")
        # Two failed rounds in a row: after each one the executor hands the
        # result in again.
        for text in ("отчёт без оценок\n", "отчёт, всё ещё без оценок\n"):
            deliver(team, claim, "a", text)
            engine.op_submit(team["root"], "t", "a", "w1", claim["execution_token"])
            engine.op_verify(team["root"], "t", "a", "fail", "нет оценок", team["lead"])
        assert task_row(team, "a")["status"] == "blocked"
        released = engine.op_action(team["root"], "t", "retry", team["lead"], task_id="a")
        assert released["status"] == "pending" and released["execution_retries"] == 1
        raised = engine.op_action(team["root"], "t", "budget", team["lead"], usd=10, confirm=True)
        assert raised["budget_usd"] == 10


# ------------------------------------------------------------ the board itself

def post(port: int, body, *, origin: str | None, content_type="application/json",
         raw: bytes | None = None, path: str = "/api/actions?team=t") -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", f"127.0.0.1:{port}")
    conn.putheader("Content-Type", content_type)
    conn.putheader("Content-Length", str(len(data)))
    if origin is not None:
        conn.putheader("Origin", origin)
    conn.endheaders(data)
    response = conn.getresponse()
    payload = response.read()
    status = response.status
    conn.close()
    return status, json.loads(payload)


class TestTheBoardActs:
    @pytest.fixture
    def board(self, team):
        server = serve(team["root"], "t", port=0, lead_token=team["lead"])
        assert server is not None
        yield {"port": server.server_address[1], **team}
        server.shutdown()
        server.server_close()

    def get(self, board, path):
        conn = http.client.HTTPConnection("127.0.0.1", board["port"], timeout=10)
        conn.request("GET", path)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def test_without_the_lead_authority_the_board_stays_read_only(self, team):
        server = serve(team["root"], "t", port=0)
        assert server is not None
        try:
            status, payload = post(server.server_address[1], {"action": "pause", "confirm": True},
                                   origin=f"http://127.0.0.1:{server.server_address[1]}")
            assert status == 405 and "read-only" in payload["error"]
            status, data = self.get({"port": server.server_address[1]},
                                    "/api/status?team=t")
            assert json.loads(data)["control"] is False
            assert task_row(team, "a")["status"] == "pending"
        finally:
            server.shutdown()
            server.server_close()

    def test_the_owner_pauses_from_the_board_and_the_token_goes_nowhere(self, board):
        port, origin = board["port"], None
        origin = f"http://127.0.0.1:{port}"
        status, payload = post(port, {"action": "pause", "confirm": True}, origin=origin)
        assert status == 200 and payload["ok"] is True and payload["paused"] is True
        assert set(payload) == {"ok", "action", "team", "paused", "already", "paused_at"}
        with pytest.raises(StateRejected, match="PAUSED"):
            engine.op_claim(board["root"], "t", "a", "w1")
        for path in ("/api/status?team=t", "/api/snapshot?team=t&level=advanced",
                     "/api/events?team=t"):
            _status, data = self.get(board, path)
            assert board["lead"].encode() not in data, path
        _status, data = self.get(board, "/api/snapshot?team=t")
        snap = json.loads(data)
        assert snap["paused"] is True and snap["run"]["state"] == "Пауза"
        assert [a["key"] for a in snap["actions"] if a["task"] is None] == ["resume"]
        _status, data = self.get(board, "/?team=t")
        assert board["lead"].encode() not in data
        status, payload = post(port, {"action": "resume"}, origin=origin)
        assert status == 200 and payload["paused"] is False
        engine.op_claim(board["root"], "t", "a", "w1")

    def test_a_request_from_another_origin_is_refused(self, board):
        status, payload = post(board["port"], {"action": "pause", "confirm": True},
                               origin="http://evil.example")
        assert status == 403 and payload["kind"] == "origin"
        status, payload = post(board["port"], {"action": "pause", "confirm": True}, origin=None)
        assert status == 403
        assert json.loads(run_row(board)["metadata_json"] or "{}") == {}

    def test_bad_requests_are_refused_in_words(self, board):
        origin = f"http://127.0.0.1:{board['port']}"
        assert post(board["port"], {}, origin=origin, content_type="text/plain")[0] == 415
        assert post(board["port"], None, origin=origin, raw=b"{not json")[0] == 400
        assert post(board["port"], {"action": "explode"}, origin=origin)[0] == 400
        assert post(board["port"], {"action": "retry"}, origin=origin)[0] == 400
        assert post(board["port"], {"action": "cancel", "task": "../x"}, origin=origin)[0] == 400
        assert post(board["port"], {"action": "budget", "usd": "ten", "confirm": True},
                    origin=origin)[0] == 400
        assert post(board["port"], None, origin=origin, raw=b"x" * 20000)[0] == 413
        assert json.loads(run_row(board)["metadata_json"] or "{}") == {}

    def test_the_engine_decides_again_when_asked(self, board):
        origin = f"http://127.0.0.1:{board['port']}"
        # No confirmation: refused before anything happens.
        status, payload = post(board["port"], {"action": "pause"}, origin=origin)
        assert status == 400 and "confirmation" in payload["error"]
        # The state moved: a stage that is already closed cannot be cancelled.
        engine.op_cancel(board["root"], "t", "b", board["lead"])
        status, payload = post(board["port"], {"action": "cancel", "task": "b", "confirm": True},
                               origin=origin)
        assert status == 409 and payload["kind"] == "state" and payload["ok"] is False
        status, payload = post(board["port"], {"action": "cancel", "task": "nobody", "confirm": True},
                               origin=origin)
        assert status == 404
        status, payload = post(board["port"], {"action": "cancel", "task": "a", "confirm": True},
                               origin=origin)
        assert status == 200 and payload["status"] == "cancelled"
        assert task_row(board, "a")["status"] == "cancelled"
