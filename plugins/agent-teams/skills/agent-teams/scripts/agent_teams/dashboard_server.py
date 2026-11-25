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
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO

from .dashboard_data import DashboardDataError, snapshot, team_dir
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
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
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
        if path not in ("/api/status", "/api/events", "/api/snapshot"):
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
            after = (query.get("after") or [None])[0]
            after_seq = int(after) if after is not None else None
            if after_seq is not None and after_seq < 0:
                raise ValueError("after must not be negative")
            snap = snapshot(root, team, level=level, event_limit=limit,
                            event_after=after_seq)
            if path == "/api/status":
                body = {"ok": True, "now": snap["now"], "run": snap["run"],
                        "progress": snap["progress"], "last_seq": snap["last_seq"],
                        "attention": len(snap["attention"]), "cost": snap["cost"]}
            elif path == "/api/events":
                body = {"ok": True, "events": snap["events"],
                        "last_seq": snap["last_seq"], "now": snap["now"]}
            else:
                body = {"ok": True, **snap}
            self._json(HTTPStatus.OK, body)
        except DashboardDataError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
        except (UsageError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is
    # already listened on; a taken port must fail there as it does on Unix.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple, project_root: Path, team: str):
        super().__init__(address, _Handler)
        self.project_root = project_root
        self.team = team

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
    thread = threading.Thread(target=server.serve_forever, name="agent-teams-ui",
                              daemon=True)
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
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
    return {"url": url, "stopped": True}
