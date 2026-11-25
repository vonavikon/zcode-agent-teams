"""The local dashboard server (v2.1 spec 16.3).

What is pinned: the server is read-only by construction (GET only, the
state files are not touched), the browser can name a team but never a
path, a Host header from another machine is refused, the page cannot
load anything from another origin, and every bad value is a clear 400.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import re
from pathlib import Path

import pytest

from agent_teams import dashboard_server, engine
from agent_teams.dashboard_server import DEFAULT_HOST, DEFAULT_PORT, _host_name, op_ui, serve
from agent_teams.errors import UsageError

PAGE = Path(dashboard_server.STATIC_DIR)
HOSTILE_TITLE = 'Отчёт <script>alert(1)</script> "кавычки" & <img src=x onerror=alert(1)>'


def spec_file(root: Path, title: str = HOSTILE_TITLE, task_id: str = "a") -> Path:
    (root / "out").mkdir(exist_ok=True)
    path = root / "spec.json"
    path.write_text(json.dumps({"tasks": [
        {"id": task_id, "title": title, "assignee": "w1", "files_write": ["out/a.md"],
         "acceptance": ["готово"], "deps": []}]}, ensure_ascii=False), encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


@pytest.fixture
def board(tmp_path):
    engine.op_init(tmp_path, "t", spec_file(tmp_path))
    server = serve(tmp_path, "t", port=0)
    assert server is not None
    yield {"root": tmp_path, "port": server.server_address[1],
           "db": tmp_path / ".agent-teams" / "t" / "state.db"}
    server.shutdown()
    server.server_close()


def call(port: int, method: str, path: str, *, host: str | None = None,
         body: bytes | None = None) -> tuple[int, dict, bytes]:
    """One raw HTTP request, so the Host header is exactly what we send."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
    if body is not None:
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    response = conn.getresponse()
    data = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    conn.close()
    return status, headers, data


def get_json(port: int, path: str, **kw) -> dict:
    status, _headers, data = call(port, "GET", path, **kw)
    assert status == 200, (status, data)
    return json.loads(data)


SNAPSHOT = "/api/snapshot?team=t"


class TestThePageCannotReachAnotherOrigin:
    def test_the_policy_forbids_everything_inline_and_remote(self, board):
        _status, headers, _data = call(board["port"], "GET", "/")
        policy = headers["content-security-policy"]
        for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                          "connect-src 'self'", "object-src 'none'", "frame-ancestors 'none'",
                          "base-uri 'none'"):
            assert directive in policy, directive
        for forbidden in ("unsafe-inline", "unsafe-eval", "http:", "https:", "*"):
            assert forbidden not in policy, forbidden
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"
        assert headers["cross-origin-resource-policy"] == "same-origin"

    def test_every_answer_carries_the_policy_even_errors(self, board):
        for path in ("/nope", "/static/nope.js", SNAPSHOT + "&limit=0",
                     "/api/snapshot?team=..%2Fx"):
            _status, headers, _data = call(board["port"], "GET", path)
            assert "content-security-policy" in headers, path
            assert headers["x-content-type-options"] == "nosniff", path

    def test_the_page_has_no_inline_script_style_or_handler(self):
        html = (PAGE / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b[^>]*>", html)
        assert scripts == ['<script src="/static/app.js">']
        assert not re.search(r"\sstyle\s*=", html)
        assert not re.search(r"\son[a-z]+\s*=", html)
        # The only http:// left is the XML namespace inside the favicon.
        external = [url for url in re.findall(r"https?://[^\"'\s>]+", html)
                    if "w3.org/2000/svg" not in url]
        assert external == []
        assert '@import' not in (PAGE / "app.css").read_text(encoding="utf-8")
        for name in ("app.js", "app.css"):
            source = (PAGE / name).read_text(encoding="utf-8")
            assert not re.search(r"https?://(?!www\.w3\.org)", source), name
            assert "eval(" not in source and "new Function(" not in source

    def test_every_asset_the_page_asks_for_is_served(self, board):
        html = (PAGE / "index.html").read_text(encoding="utf-8")
        for asset in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
            status, headers, data = call(board["port"], "GET", asset)
            assert status == 200 and data, asset
            expected = "text/css; charset=utf-8" if asset.endswith(".css") \
                else "text/javascript; charset=utf-8"
            assert headers["content-type"] == expected, asset

    def test_the_stylesheet_is_whole(self):
        # An unclosed block in a stylesheet takes everything after it down.
        css = (PAGE / "app.css").read_text(encoding="utf-8")
        assert css.count("{") == css.count("}")
        assert css.count("/*") == css.count("*/")
        assert "640px" in css and "@container" in css

    def test_no_long_dash_in_what_the_reader_sees(self):
        for name in ("index.html", "app.js"):
            source = (PAGE / name).read_text(encoding="utf-8")
            assert "\u2014" not in source and "\u2013" not in source, name


class TestReadOnlyByConstruction:
    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    def test_nothing_but_get_and_head(self, board, method):
        before = sha(board["db"])
        status, _headers, data = call(board["port"], method, SNAPSHOT, body=b"x=1")
        assert status == 405
        assert json.loads(data)["error"] == "read-only: the dashboard serves GET only"
        assert sha(board["db"]) == before

    def test_a_burst_of_requests_leaves_the_database_as_it_was(self, board):
        wal = Path(str(board["db"]) + "-wal")
        had_wal = wal.exists()
        before = (sha(board["db"]), sha(wal))
        for _ in range(20):
            for path in ("/", "/static/app.js", "/static/app.css", SNAPSHOT,
                         "/api/status?team=t", "/api/events?team=t", "/api/health"):
                assert call(board["port"], "GET", path)[0] == 200, path
        assert sha(board["db"]) == before[0]
        # The database is never written. SQLite may create its own empty
        # journal sidecar to read a WAL database; a journal that existed
        # keeps its bytes, one that appears is empty.
        if had_wal:
            assert sha(wal) == before[1]
        else:
            assert wal.stat().st_size == 0 if wal.exists() else True

    def test_head_answers_like_get_without_a_body(self, board):
        status, headers, data = call(board["port"], "HEAD", SNAPSHOT)
        assert status == 200 and data == b""
        assert int(headers["content-length"]) > 100


class TestTheBrowserNamesATeamNeverAPath:
    def test_the_project_root_comes_from_the_command_not_from_the_request(self, board, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        engine.op_init(other, "t", spec_file(other, title="Чужая команда"))
        served = get_json(board["port"], SNAPSHOT + "&project_root=" + str(other))
        assert served["tasks"][0]["title"] == HOSTILE_TITLE
        # Without the parameter it is the same team, as it should be.
        assert get_json(board["port"], SNAPSHOT)["tasks"][0]["title"] == HOSTILE_TITLE

    def test_another_team_of_the_same_project_can_be_named(self, board):
        engine.op_init(board["root"], "second", spec_file(board["root"], title="Вторая"))
        assert get_json(board["port"], "/api/snapshot?team=second")["team"] == "second"
        assert get_json(board["port"], SNAPSHOT)["team"] == "t"

    @pytest.mark.parametrize("team, status", [
        ("", 400), ("..%2F..%2Fetc", 400), ("a/b", 400), ("x" * 65, 400),
        ("nobody", 404),
    ])
    def test_a_bad_or_unknown_team_is_a_clear_answer(self, board, team, status):
        got, _headers, _data = call(board["port"], "GET", f"/api/snapshot?team={team}")
        assert got == status, team

    @pytest.mark.parametrize("query, status", [
        ("limit=0", 400), ("limit=1001", 400), ("limit=abc", 400),
        ("after=-1", 400), ("after=abc", 400), ("", 200), ("limit=1000", 200),
    ])
    def test_the_window_of_events_has_bounds(self, board, query, status):
        got, _headers, data = call(board["port"], "GET", f"/api/events?team=t&{query}")
        assert got == status, query
        if status == 400:
            assert json.loads(data)["ok"] is False

    def test_an_unknown_level_is_the_simple_one(self, board):
        assert get_json(board["port"], SNAPSHOT + "&level=nonsense")["level"] == "simple"
        assert "tech" not in get_json(board["port"], SNAPSHOT + "&level=nonsense")
        assert "tech" in get_json(board["port"], SNAPSHOT + "&level=advanced")


class TestAnotherMachineIsRefused:
    @pytest.mark.parametrize("host", [
        "evil.example", "evil.example:8765", "127.0.0.1.evil.example",
        "localhost.evil.example", "127.0.0.1@evil.example", "[::1].evil.example",
    ])
    def test_a_host_that_names_another_machine_gets_nothing(self, board, host):
        for path in ("/", SNAPSHOT, "/api/health"):
            status, _headers, data = call(board["port"], "GET", path, host=host)
            assert status == 403, (path, host)
            assert json.loads(data) == {"error": "unknown Host"}

    @pytest.mark.parametrize("host", ["localhost", "localhost:8765", "LOCALHOST:1", "127.0.0.1"])
    def test_a_loopback_name_is_served(self, board, host):
        assert call(board["port"], "GET", "/api/health", host=host)[0] == 200

    def test_a_server_meant_for_the_network_is_reached_by_name(self, tmp_path):
        engine.op_init(tmp_path, "t", spec_file(tmp_path))
        # Bound to a real address on purpose: named hosts are the point.
        server = serve(tmp_path, "t", host="0.0.0.0", port=0)
        assert server is not None
        try:
            port = server.server_address[1]
            assert call(port, "GET", "/api/health", host="my-laptop.local:4321")[0] == 200
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize("header, name", [
        ("127.0.0.1:8765", "127.0.0.1"), ("localhost", "localhost"),
        ("[::1]:8765", "::1"), ("[::1]", "::1"), ("127.0.0.1", "127.0.0.1"),
        ("example.com:abc", "example.com:abc"),
    ])
    def test_the_name_is_read_from_any_host_form(self, header, name):
        assert _host_name(header) == name


class TestTheAnswers:
    def test_health_needs_no_team(self, board):
        assert get_json(board["port"], "/api/health") == {"ok": True, "service": "agent-teams-ui"}

    def test_the_snapshot_is_the_whole_board(self, board):
        snap = get_json(board["port"], SNAPSHOT)
        assert snap["ok"] is True and snap["team"] == "t"
        assert {"run", "progress", "tasks", "agents", "attention", "events", "last_seq",
                "cost", "elapsed_s", "now", "level", "finished"} <= set(snap)

    def test_status_and_events_are_the_light_answers(self, board):
        status = get_json(board["port"], "/api/status?team=t")
        assert set(status) == {"ok", "now", "run", "progress", "last_seq", "attention", "cost"}
        events = get_json(board["port"], "/api/events?team=t")
        assert set(events) == {"ok", "events", "last_seq", "now"}
        newest = events["last_seq"]
        assert get_json(board["port"], f"/api/events?team=t&after={newest}")["events"] == []

    def test_markup_in_a_title_travels_as_text_and_back_unchanged(self, board):
        snap = get_json(board["port"], SNAPSHOT)
        assert snap["tasks"][0]["title"] == HOSTILE_TITLE
        _status, headers, _data = call(board["port"], "GET", SNAPSHOT)
        assert headers["content-type"] == "application/json; charset=utf-8"

    def test_an_unknown_endpoint_is_not_found(self, board):
        assert call(board["port"], "GET", "/api/nothing?team=t")[0] == 404
        assert call(board["port"], "GET", "/api/nothing")[0] == 404

    @pytest.mark.parametrize("path", [
        "/static/../viewmodel.py", "/static/%2e%2e/viewmodel.py",
        "/static/..%2f..%2f..%2fcli.py", "/static/..%5c..%5ccli.py",
        "/static/%2e%2e%2f%2e%2e%2fcli.py", "/static/./../../cli.py",
        "/viewmodel.py", "/.agent-teams/t/state.db",
    ])
    def test_nothing_outside_the_page_directory_is_served(self, board, path):
        status, _headers, data = call(board["port"], "GET", path)
        assert status in (403, 404), (path, status)
        assert b"def " not in data and b"SQLite format" not in data

    def test_the_server_does_not_say_what_it_runs_on(self, board):
        _status, headers, _data = call(board["port"], "GET", "/")
        assert headers["server"] == "agent-teams-ui"
        assert "Python" not in headers["server"]


class TestOneResourcePerAddress:
    """The board's addresses of v2.1 spec 16.3 that its data is ready for."""

    def test_tasks_agents_and_cost_are_served_apart(self, board):
        tasks = get_json(board["port"], "/api/tasks?team=t")
        assert tasks["ok"] is True and tasks["level"] == "simple"
        assert [t["title"] for t in tasks["tasks"]] == [HOSTILE_TITLE]
        agents = get_json(board["port"], "/api/agents?team=t")
        assert [a["name"] for a in agents["agents"]] == ["AI-специалист"]
        cost = get_json(board["port"], "/api/cost?team=t")
        assert set(cost) == {"ok", "cost", "level", "now"} and cost["cost"]["usd"] == 0.0

    def test_one_task_comes_with_its_own_history(self, board):
        engine.op_claim(board["root"], "t", "a", "w1")
        got = get_json(board["port"], "/api/tasks/a?team=t")
        assert got["task"]["id"] == "a" and got["task"]["title"] == HOSTILE_TITLE
        assert [e["title"] for e in got["events"]] == ["Приступил к задаче"]
        assert got["task"]["next_step"] == "Специалист работает"
        advanced = get_json(board["port"], "/api/tasks/a?team=t&level=advanced")
        assert [e["title"] for e in advanced["events"]] == [
            "Задача добавлена в план", "Приступил к задаче", "Специалист приступил к работе"]
        assert advanced["task"]["tech"]["assignee"] == "w1"

    def test_an_unknown_task_is_not_found_and_a_bad_id_is_a_bad_request(self, board):
        status, _headers, data = call(board["port"], "GET", "/api/tasks/nope?team=t")
        assert status == 404 and json.loads(data) == {"ok": False, "error": "task 'nope' not found"}
        for bad in ("a%2Fb", "x" * 65, "%20"):
            assert call(board["port"], "GET", f"/api/tasks/{bad}?team=t")[0] == 400, bad
        assert call(board["port"], "GET", "/api/tasks/?team=t")[0] == 200   # the list itself
        # A path that climbs out of the address is simply no task address.
        assert call(board["port"], "GET", "/api/tasks/..%2Fstate.db?team=t")[0] == 404

    def test_they_are_read_only_and_behind_the_same_host_rule(self, board):
        before = sha(board["db"])
        for path in ("/api/tasks?team=t", "/api/tasks/a?team=t", "/api/agents?team=t",
                     "/api/cost?team=t"):
            assert call(board["port"], "POST", path, body=b"")[0] == 405, path
            assert call(board["port"], "GET", path, host="evil.example")[0] == 403, path
        assert sha(board["db"]) == before


class TestStarting:
    def test_a_taken_port_is_reported_not_crashed(self, board):
        assert serve(board["root"], "t", port=board["port"]) is None

    def test_the_team_must_exist(self, tmp_path):
        with pytest.raises(UsageError, match="has no state"):
            serve(tmp_path, "nobody", port=0)
        with pytest.raises(UsageError, match="invalid team name"):
            serve(tmp_path, "bad/name", port=0)

    def test_the_defaults_are_this_machine_only(self):
        assert (DEFAULT_HOST, DEFAULT_PORT) == ("127.0.0.1", 8765)

    def test_ui_announces_its_address_and_stops_cleanly(self, tmp_path, monkeypatch):
        engine.op_init(tmp_path, "t", spec_file(tmp_path))
        interrupted = {"count": 0}

        def serve_until_interrupted(self, **_options):
            interrupted["count"] += 1
            raise KeyboardInterrupt

        monkeypatch.setattr(dashboard_server.DashboardServer, "serve_forever",
                            serve_until_interrupted)
        out = io.StringIO()
        result = op_ui(tmp_path, "t", port=0, out=out)
        assert result["stopped"] is True
        assert result["url"].startswith("http://127.0.0.1:")
        assert out.getvalue() == f"dashboard: {result['url']}\nread-only; stops with Ctrl-C\n"
        assert interrupted["count"] == 1

    def test_ui_in_json_mode_prints_one_json_line(self, tmp_path, monkeypatch):
        engine.op_init(tmp_path, "t", spec_file(tmp_path))
        monkeypatch.setattr(dashboard_server.DashboardServer, "serve_forever",
                            lambda self, **_options: (_ for _ in ()).throw(KeyboardInterrupt))
        out = io.StringIO()
        result = op_ui(tmp_path, "t", port=0, json_mode=True, out=out)
        assert json.loads(out.getvalue()) == {"url": result["url"], "read_only": True}

    def test_ui_on_the_network_warns_first(self, tmp_path, monkeypatch):
        engine.op_init(tmp_path, "t", spec_file(tmp_path))
        monkeypatch.setattr(dashboard_server.DashboardServer, "serve_forever",
                            lambda self, **_options: (_ for _ in ()).throw(KeyboardInterrupt))
        out = io.StringIO()
        op_ui(tmp_path, "t", host="0.0.0.0", port=0, out=out)
        assert out.getvalue().startswith("warning: bound to 0.0.0.0")

    def test_a_taken_port_is_a_usage_error_for_ui(self, board):
        with pytest.raises(UsageError, match="already in use"):
            op_ui(board["root"], "t", port=board["port"], out=io.StringIO())
