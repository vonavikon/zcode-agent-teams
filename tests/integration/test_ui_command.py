"""``team.py ui`` as a real process (v2.1 spec 16).

The command must print the address before it starts waiting, serve the
team it was pointed at, answer over HTTP, and stop cleanly on Ctrl-C.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts"
TEAM = SCRIPTS / "team.py"


def init(root: Path, title: str) -> None:
    (root / "out").mkdir(parents=True, exist_ok=True)
    spec = root / "spec.json"
    spec.write_text(json.dumps({"tasks": [
        {"id": "a", "title": title, "assignee": "w1", "files_write": ["out/a.md"],
         "acceptance": ["готово"], "deps": []}]}, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TEAM), "init", "--team", "t", "--tasks", str(spec),
         "--project-root", str(root)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr


@pytest.fixture
def project(tmp_path):
    init(tmp_path, "Собрать отчёт")
    return tmp_path


def start(root: Path, *extra: str) -> subprocess.Popen:
    # Port 0: the real port is known only from what the process prints.
    return subprocess.Popen(
        [sys.executable, str(TEAM), "ui", "--team", "t", "--project-root", str(root),
         "--port", "0", *extra],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def read_line(proc: subprocess.Popen, timeout: float = 60.0) -> str:
    """One line of the process output, or a failure instead of an endless wait."""
    box: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: box.put(proc.stdout.readline()), daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError("the dashboard process printed nothing in time") from None


def origin(url: str) -> str:
    """The announced address without the page path: the API lives there."""
    return url.split("/?")[0]


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def stop(proc: subprocess.Popen) -> int:
    if os.name == "nt":     # a console interrupt cannot be sent to a child there
        proc.terminate()
    else:
        proc.send_signal(signal.SIGINT)
    return proc.wait(timeout=20)


def test_announces_the_address_and_serves_the_team(project):
    proc = start(project, "--json")
    try:
        announced = json.loads(read_line(proc))
        assert announced["read_only"] is True
        assert announced["url"].startswith("http://127.0.0.1:")
        assert get(origin(announced["url"]) + "/api/health") == {
            "ok": True, "service": "agent-teams-ui"}
        snap = get(origin(announced["url"]) + "/api/snapshot?team=t")
        assert [t["title"] for t in snap["tasks"]] == ["Собрать отчёт"]
        with urllib.request.urlopen(announced["url"], timeout=10) as page:
            assert b"/static/app.js" in page.read()
    finally:
        code = stop(proc)
    if os.name != "nt":
        assert code == 0, proc.stderr.read()   # Ctrl-C is a clean stop
    assert "Traceback" not in proc.stderr.read()


def test_plain_mode_prints_two_lines_for_a_person(project):
    proc = start(project)
    try:
        first = read_line(proc).strip()
        second = read_line(proc).strip()
        assert first.startswith("dashboard: http://127.0.0.1:") and "team=t" in first
        assert second == "read-only; stops with Ctrl-C"
        assert get(origin(first.split("dashboard: ", 1)[1]) + "/api/health")["ok"]
    finally:
        stop(proc)


def test_the_browser_cannot_point_it_at_another_project(project, tmp_path):
    init(tmp_path / "other", "Чужая задача")
    proc = start(project, "--json")
    try:
        url = json.loads(read_line(proc))["url"]
        served = get(origin(url) + "/api/snapshot?team=t&project_root=" + str(tmp_path / "other"))
        assert [t["title"] for t in served["tasks"]] == ["Собрать отчёт"]
    finally:
        stop(proc)


def test_a_team_that_does_not_exist_is_refused_before_the_port_is_open(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(TEAM), "ui", "--team", "nobody", "--project-root", str(tmp_path),
         "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode != 0
    assert "has no state" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_taken_port_is_said_plainly(project):
    first = start(project, "--json")
    try:
        port = origin(json.loads(first.stdout.readline())["url"]).rsplit(":", 1)[1]
        second = subprocess.run(
            [sys.executable, str(TEAM), "ui", "--team", "t", "--project-root", str(project),
             "--json", "--port", port],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert second.returncode != 0
        assert f"port {port} is already in use" in second.stderr
        assert "Traceback" not in second.stderr
    finally:
        stop(first)
