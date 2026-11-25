"""Local web dashboard server (spec v2.1, section 16).

Python stdlib only: ``http.server`` on a thread, SQLite read through
``dashboard_data``. Binds to 127.0.0.1 by default, serves the dashboard
assets and a read-only JSON API, and never changes team state.

Security posture, by construction:
- loopback bind unless the owner asks for another host explicitly;
- the project root is fixed by the command that started the server; the
  browser can only name a team inside it, never a path;
- GET only, every path resolved inside the static directory (no traversal);
- the state database is never written. SQLite itself may create its
  empty ``-shm`` and ``-wal`` sidecars next to it when a read-only
  connection opens a WAL database for the first time: that is the
  engine's bookkeeping, not team state;
- JSON responses carry ``nosniff`` and a CSP that forbids any remote
  origin and any inline script or style, so the page cannot load a CDN
  or an analytics beacon;
- while bound to loopback, a ``Host`` header that names another machine
  is refused: that is what a rebinding attack looks like;
- no secret reaches the frontend: the payload is built from a column
  whitelist, and the lead token is never read here.
"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import signal
import socketserver
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO

from .dashboard_data import DashboardDataError, result_file, snapshot, team_dir
from .errors import UsageError
from .validation import NAME_RE

STATIC_DIR = Path(__file__).resolve().parent / "dashboard"
# Fixed on purpose: mimetypes answers differently from one Python to the next.
STATIC_TYPES = {".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self';"
       " img-src 'self' data:; connect-src 'self'; font-src 'self';"
       " object-src 'none'; base-uri 'none'; form-action 'none';"
       " frame-ancestors 'none'")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# The read-only answers of the board (v2.1 spec 16.3). One task has its
# own address, /api/tasks/<id>, and one file of a result another,
# /api/results/<task>/<file>.
ENDPOINTS = ("/api/status", "/api/events", "/api/snapshot", "/api/tasks",
             "/api/agents", "/api/cost", "/api/metrics", "/api/recovery",
             "/api/integration", "/api/artifacts", "/api/benchmark")
# A file of a result is the team's own content. It is shown as text or as
# a picture and is never allowed to act as a page of the board.
RESULT_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
_LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}


def _host_name(header: str) -> str:
    """The machine name part of a ``Host`` header (``[::1]:8765`` -> ``::1``).

    Anything that is not a plain name with an optional port is returned
    as it came, so it matches no allowed name: ``[::1].evil.example``
    must not pass for ``::1``.
    """
    header = header.strip().lower()
    if header.startswith("["):
        end = header.find("]")
        if end == -1:
            return header
        rest = header[end + 1:]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return header
        return header[1:end]
    name, sep, port = header.rpartition(":")
    return name if sep and name and port.isdigit() else header


class _Handler(BaseHTTPRequestHandler):
    server_version = "agent-teams-ui"

    def version_string(self) -> str:
        # The reader of the header does not need the Python version.
        return self.server_version

    def log_message(self, fmt, *args):  # quiet by default
        return

    # -- helpers ---------------------------------------------------------

    def _send(self, status: HTTPStatus, body: bytes, ctype: str,
              extra: dict | None = None, policy: str = CSP) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _static(self, rel: str) -> None:
        root = STATIC_DIR.resolve()
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        if not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        ctype = STATIC_TYPES.get(target.suffix) or mimetypes.guess_type(str(target))[0] \
            or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    def _host_allowed(self) -> bool:
        """A loopback server answers only to a loopback name.

        A page on another site can point its own domain at 127.0.0.1 and
        read the answer as same-origin. The ``Host`` it sends is that
        other name, so the request is refused here. A server bound to a
        real address is meant to be reached by name and is not checked.
        """
        if self.server.server_address[0] not in _LOOPBACK_NAMES:
            return True
        return _host_name(self.headers.get("Host") or "") in _LOOPBACK_NAMES

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if not self._host_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"error": "unknown Host"})
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = posixpath.normpath(urllib.parse.unquote(parsed.path))
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/" or path == "/index.html":
            self._static("index.html")
            return
        if path.startswith("/api/"):
            self._api(path, query)
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _unsupported(self) -> None:  # noqa: N802
        self._json(HTTPStatus.METHOD_NOT_ALLOWED,
                   {"error": "read-only: the dashboard serves GET only"})
        self.close_connection = True

    do_POST = do_PUT = do_DELETE = do_PATCH = _unsupported

    def _api(self, path: str, query: dict) -> None:
        if path == "/api/health":  # a liveness probe names no team
            self._json(HTTPStatus.OK, {"ok": True, "service": "agent-teams-ui"})
            return
        if not path.startswith(("/api/tasks/", "/api/results/")) and path not in ENDPOINTS:
            self._json(HTTPStatus.NOT_FOUND,
                       {"ok": False, "error": f"unknown endpoint {path}"})
            return
        # The project root comes from the server, never from the request.
        root = self.server.project_root
        team = (query.get("team") or [""])[0]
        if not NAME_RE.match(team):
            self._json(HTTPStatus.BAD_REQUEST,
                       {"ok": False, "error": "a valid team name is required"})
            return
        try:
            level = (query.get("level") or ["simple"])[0]
            limit = int((query.get("limit") or ["200"])[0])
            if not 1 <= limit <= 1000:
                raise ValueError("limit must be between 1 and 1000")
            cursors = {}
            for name in ("after", "before"):
                value = (query.get(name) or [None])[0]
                cursors[name] = int(value) if value is not None else None
                if cursors[name] is not None and cursors[name] < 0:
                    raise ValueError(f"{name} must not be negative")
            after_seq = cursors["after"]
            if path.startswith("/api/results/"):
                self._result(path, root, team)
                return
            if path.startswith("/api/tasks/"):
                task_id = urllib.parse.unquote(path[len("/api/tasks/"):])
                if not NAME_RE.match(task_id):
                    self._json(HTTPStatus.BAD_REQUEST,
                               {"ok": False, "error": "a valid task id is required"})
                    return
                snap = snapshot(root, team, level=level, event_limit=1000,
                                parts=("task_events",))
                task = next((t for t in snap["tasks"] if t["id"] == task_id), None)
                if task is None:
                    self._json(HTTPStatus.NOT_FOUND,
                               {"ok": False, "error": f"task {task_id!r} not found"})
                    return
                body = {"ok": True, "task": task, "level": snap["level"],
                        "now": snap["now"],
                        "events": snap["task_events"].get(task_id, [])}
            else:
                snap = snapshot(root, team, level=level, event_limit=limit,
                                event_after=after_seq, event_before=cursors["before"])
                if path == "/api/status":
                    body = {"ok": True, "now": snap["now"], "run": snap["run"],
                            "progress": snap["progress"], "last_seq": snap["last_seq"],
                            "attention": len(snap["attention"]), "cost": snap["cost"]}
                elif path == "/api/events":
                    body = {"ok": True, "events": snap["events"],
                            "last_seq": snap["last_seq"], "now": snap["now"]}
                elif path == "/api/tasks":
                    body = {"ok": True, "tasks": snap["tasks"], "level": snap["level"],
                            "now": snap["now"]}
                elif path == "/api/agents":
                    body = {"ok": True, "agents": snap["agents"], "level": snap["level"],
                            "now": snap["now"]}
                elif path == "/api/cost":
                    body = {"ok": True, "cost": snap["cost"], "budget": snap["budget"],
                            "level": snap["level"], "now": snap["now"]}
                elif path == "/api/metrics":
                    body = {"ok": True, "quality": snap["quality"], "recovery": snap["recovery"],
                            "integration": snap["integration"], "budget": snap["budget"],
                            "cost": snap["cost"], "progress": snap["progress"],
                            "level": snap["level"], "now": snap["now"]}
                elif path == "/api/recovery":
                    body = {"ok": True, "recovery": snap["recovery"],
                            "attention": snap["attention"], "level": snap["level"],
                            "now": snap["now"]}
                elif path == "/api/integration":
                    body = {"ok": True, "integration": snap["integration"], "run": snap["run"],
                            "level": snap["level"], "now": snap["now"]}
                elif path == "/api/benchmark":
                    body = {"ok": True, "benchmark": snap.get("benchmark"),
                            "level": snap["level"], "now": snap["now"]}
                elif path == "/api/artifacts":
                    body = {"ok": True, "level": snap["level"], "now": snap["now"],
                            "artifacts": [{"task": t["id"], "title": t["title"],
                                           "ready": t["result"]["ready"],
                                           "handed_over": t["result"]["handed_over"],
                                           "files": t["result"]["files"]}
                                          for t in snap["tasks"]]}
                else:
                    body = {"ok": True, **snap}
            self._json(HTTPStatus.OK, body)
        except DashboardDataError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
        except (UsageError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    def _result(self, path: str, root: Path, team: str) -> None:
        """One file of a task's result, as the engine pinned it (spec 8.5)."""
        rest = path[len("/api/results/"):]
        task_id, _, name = rest.partition("/")
        if not NAME_RE.match(task_id) or not name:
            self._json(HTTPStatus.BAD_REQUEST,
                       {"ok": False, "error": "a task id and a file name are required"})
            return
        try:
            found = result_file(root, team, task_id, name)
        except DashboardDataError as exc:
            too_large = "too large" in str(exc)
            # 413 by number: its name in the standard library differs
            # between Python 3.10 and 3.11.
            self._json(413 if too_large else HTTPStatus.NOT_FOUND,
                       {"ok": False, "error": str(exc)})
            return
        if found is None:
            self._json(HTTPStatus.NOT_FOUND,
                       {"ok": False, "error": f"{name!r} is not part of the result of task {task_id!r}"})
            return
        data, ctype = found
        shown = ctype.startswith(("text/", "image/"))
        self._send(HTTPStatus.OK, data, ctype, policy=RESULT_CSP,
                   extra={} if shown else {"Content-Disposition": "attachment"})


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is
    # already listened on; a taken port must fail there as it does on Unix.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple, project_root: Path, team: str):
        super().__init__(address, _Handler)
        self.project_root = project_root
        self.team = team

    def server_bind(self):
        # HTTPServer resolves the bind address into a domain name here.
        # That is a network lookup: slow where the resolver does not
        # answer, and the board works offline and answers by address.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port

    def handle_error(self, request, client_address):
        # A browser that gave up on a request (a closed tab, an aborted
        # poll) closes the connection; that is not a server error.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _bind(project_root: Path, team: str, host: str, port: int) -> DashboardServer | None:
    """Validate the scope and bind; None when the port is taken."""
    if not NAME_RE.match(team or ""):
        raise UsageError(f"invalid team name: {team!r}")
    if not team_dir(project_root, team).exists():
        raise UsageError(f"team {team!r} has no state at"
                        f" {team_dir(project_root, team)}; run init first")
    try:
        return DashboardServer((host, port), project_root, team)
    except OSError:
        return None


def _url(host: str, port: int, team: str) -> str:
    shown = host if host not in ("0.0.0.0", "::") else "127.0.0.1"
    return (f"http://{shown}:{port}/?team={urllib.parse.quote(team)}")


def serve(project_root: Path, team: str, *, host: str = DEFAULT_HOST,
          port: int = DEFAULT_PORT) -> DashboardServer | None:
    """Start the dashboard in a background thread and return the server.

    Returns None when the port is taken (the caller reports it). The
    server runs until ``shutdown()`` is called.
    """
    server = _bind(Path(project_root).resolve(), team, host, port)
    if server is None:
        return None
    # A short poll keeps shutdown() quick for whoever embeds the server;
    # the blocking ui command waits with its own interval.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05},
        name="agent-teams-ui", daemon=True)
    thread.start()
    return server


def op_ui(root: Path, team: str, *, host: str = DEFAULT_HOST,
          port: int = DEFAULT_PORT, open_browser: bool = False,
          json_mode: bool = False, out: TextIO | None = None) -> dict:
    """Run the dashboard until the process is interrupted.

    The address is written before the server starts waiting, so the lead
    sees it without a second terminal. Returns when the user interrupts
    the process; the server is closed by then.
    """
    out = sys.stdout if out is None else out
    project_root = Path(root).resolve()
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: bound to {host}; the dashboard is reachable from"
              " other machines on this network", file=out, flush=True)
    server = _bind(project_root, team, host, port)
    if server is None:
        raise UsageError(f"port {port} is already in use")
    with server:
        url = _url(host, server.server_address[1], team)
        if json_mode:
            line = json.dumps({"url": url, "read_only": True}, ensure_ascii=False)
        else:
            line = f"dashboard: {url}\nread-only; stops with Ctrl-C"
        try:
            print(line, file=out, flush=True)
            if open_browser:
                import webbrowser
                webbrowser.open(url)
            # A script that starts the board in the background leaves the
            # interrupt ignored in this process, and Python keeps what it
            # inherits: Ctrl-C would do nothing, and the command promises
            # to stop on it. The signal is taken back; nothing else is.
            if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
                signal.signal(signal.SIGINT, signal.default_int_handler)
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
    return {"url": url, "stopped": True}
