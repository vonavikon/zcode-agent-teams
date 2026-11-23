"""CLI smoke test: ateam.py as a real subprocess, exit codes included."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ATEAM = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts/ateam.py"


def run(*args, root: Path):
    proc = subprocess.run(
        [sys.executable, str(ATEAM), *args, "--project-root", str(root)],
        capture_output=True, text=True, timeout=60)
    return proc


def spec_file(root: Path):
    path = root / "spec.json"
    path.write_text(json.dumps({"tasks": [{
        "id": "a", "title": "demo", "assignee": "w1",
        "files_write": ["out/a.md"], "acceptance": ["done"], "deps": []}]}),
        encoding="utf-8")
    return path


def test_full_cli_flow_with_exit_codes(tmp_path):
    (tmp_path / "out").mkdir()
    spec = spec_file(tmp_path)

    proc = run("init", "--team", "t", "--tasks", str(spec), root=tmp_path)
    assert proc.returncode == 0, proc.stderr
    lead_token = [line.split()[-1] for line in proc.stdout.splitlines()
                  if line.startswith("lead token")][0]

    proc = run("claim", "--team", "t", "--task", "a", "--by", "w1", root=tmp_path)
    assert proc.returncode == 0, proc.stderr
    token = [line.split()[-1] for line in proc.stdout.splitlines()
             if line.startswith("execution token")][0]

    proc = run("submit", "--team", "t", "--task", "a", "--by", "w1",
               "--token", token, root=tmp_path)
    assert proc.returncode == 0, proc.stderr

    # a forged lead token is exit code 5, not a traceback
    proc = run("verify", "--team", "t", "--task", "a", "--verdict", "pass",
               "--lead-token", "forged", root=tmp_path)
    assert proc.returncode == 5
    assert "invalid lead token" in proc.stderr
    assert "Traceback" not in proc.stderr

    proc = run("verify", "--team", "t", "--task", "a", "--verdict", "pass",
               "--lead-token", lead_token, root=tmp_path)
    assert proc.returncode == 0, proc.stderr

    proc = run("status", "--team", "t", "--json", root=tmp_path)
    assert proc.returncode == 0
    status = json.loads(proc.stdout)
    assert status["tasks"][0]["status"] == "completed"

    # mirrors exist in the v0.2 shapes
    team_dir = tmp_path / ".agent-teams" / "t"
    mirror = json.loads((team_dir / "tasks.json").read_text(encoding="utf-8"))
    assert mirror["tasks"][0]["status"] == "completed"
    assert (team_dir / "events.jsonl").exists()

    # unknown team is exit code 4
    proc = run("status", "--team", "ghost", root=tmp_path)
    assert proc.returncode == 4

    # a bad spec is exit code 1 with all issues listed
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tasks": [{"id": "a", "title": "x",
                                          "assignee": "w1", "acceptance": []}]}),
                   encoding="utf-8")
    proc = run("init", "--team", "bad", "--tasks", str(bad), root=tmp_path)
    assert proc.returncode == 1
    assert "E_ACCEPTANCE_EMPTY" in proc.stderr
