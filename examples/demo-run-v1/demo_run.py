#!/usr/bin/env python3
"""Demo run of agent-teams v1.0: three concurrent executor processes
through the real CLI, one FAIL->fix->PASS round, the integration gate
with a check command, an HTML report and metrics.

LLM teammates are NOT used (the model provider was rate-limited at demo
time); the executors are plain processes following the teammate contract.
The engine path (tokens, worktrees, gate, integration) is identical.
"""
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path("/tmp/zat-demo/project")
OUT = Path("/tmp/zat-demo/artifacts")
TEAM = "demo-v1"
SCRIPTS = Path("/Users/kivanov/repos/zcode-agent-teams/plugins/agent-teams/"
               "skills/agent-teams/scripts")
CLI = [sys.executable, str(SCRIPTS / "team.py")]


def run(*args, expect=0):
    proc = subprocess.run([*CLI, *(str(a) for a in args),
                          "--project-root", str(ROOT)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == expect, (args, proc.returncode, proc.stderr)
    return proc.stdout


def git(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def lens_task(tid, assignee):
    return {"id": tid, "title": f"{tid} review", "assignee": assignee,
            "lens": tid.replace("lens-", ""), "files_read": ["README.md"],
            "files_write": [f"reports/{tid.replace('lens-', '')}.md"],
            "acceptance": [
                "report exists with an evidenced finding",
                {"id": "AC-1", "description": "report file present",
                 "type": "file_check",
                 "verification": f"test -f reports/{tid.replace('lens-', '')}.md"}],
            "deps": []}


def executor(name, task, results):
    out = run("claim", "--team", TEAM, "--task", task, "--by", name)
    token = [l.split()[-1] for l in out.splitlines()
             if l.startswith("execution token")][0]
    workspace = ROOT
    for line in out.splitlines():
        if "workspace=" in line:
            workspace = Path(line.split("workspace=")[-1])
    results[name] = {"task": task, "token": token, "workspace": workspace}
    report = workspace / "reports" / f"{task.replace('lens-', '')}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# {task}\n\nfindings by {name}\n- F1 example\n",
                      encoding="utf-8")
    if workspace != ROOT:
        git(workspace, "add", "-A")
        git(workspace, "commit", "-qm", f"{task} first pass")
    run("submit", "--team", TEAM, "--task", task, "--by", name,
        "--token", token)


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    if OUT.exists():
        shutil.rmtree(OUT)
    ROOT.mkdir(parents=True)
    OUT.mkdir(parents=True)
    (ROOT / "reports").mkdir()
    (ROOT / ".gitignore").write_text(".agent-teams/\n", encoding="utf-8")
    (ROOT / "check.sh").write_text(
        "#!/bin/sh\nls reports/security.md reports/perf.md reports/coverage.md"
        " >/dev/null\n", encoding="utf-8")
    subprocess.run(["chmod", "+x", str(ROOT / "check.sh")], check=True)
    git(ROOT, "init", "-q", "-b", "main")
    git(ROOT, "config", "user.email", "demo@example.invalid")
    git(ROOT, "config", "user.name", "demo")
    (ROOT / "README.md").write_text("demo project\n", encoding="utf-8")
    git(ROOT, "add", "-A")
    git(ROOT, "commit", "-qm", "base")

    spec = {"tasks": [lens_task("lens-security", "sec"),
                      lens_task("lens-perf", "perf"),
                      lens_task("lens-coverage", "cov")]}
    (ROOT / "tasks.json").write_text(json.dumps(spec, indent=2),
                                     encoding="utf-8")

    out = run("init", "--team", TEAM, "--tasks", str(ROOT / "tasks.json"))
    lead_token = [l.split()[-1] for l in out.splitlines()
                  if l.startswith("lead token")][0]
    (OUT / "init.txt").write_text(out, encoding="utf-8")

    results = {}
    threads = [threading.Thread(target=executor, args=(n, t, results))
               for n, t in (("sec", "lens-security"), ("perf", "lens-perf"),
                            ("cov", "lens-coverage"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The gate. security gets an honest FAIL round first (findings without
    # a file:line reference); the executor fixes in the same open attempt
    # (same worktree, same execution token) and resubmits.
    run("verify", "--team", TEAM, "--task", "lens-security", "--verdict",
        "fail", "--feedback", "findings need a file:line reference",
        "--lead-token", lead_token)
    (OUT / "mailbox-sec-after-fail.json").write_text(
        run("read", "--team", TEAM, "--for", "sec"), encoding="utf-8")
    sec = results["sec"]
    report = sec["workspace"] / "reports" / "security.md"
    report.write_text("# lens-security\n\nfindings by sec\n"
                      "- F1 README.md:1 demo finding with a reference\n",
                      encoding="utf-8")
    git(sec["workspace"], "add", "-A")
    git(sec["workspace"], "commit", "-qm", "security: add file:line reference")
    run("submit", "--team", TEAM, "--task", "lens-security", "--by", "sec",
        "--token", sec["token"])

    for task in ("lens-security", "lens-perf", "lens-coverage"):
        run("verify", "--team", TEAM, "--task", task, "--verdict", "pass",
            "--lead-token", lead_token)

    run("tokens", "--team", TEAM, "--task", "lens-security", "--add", 41000,
        "--by", "sec", "--model", "glm-5.4")
    run("tokens", "--team", TEAM, "--task", "lens-perf", "--add", 38000,
        "--by", "perf", "--model", "glm-5.4")
    run("tokens", "--team", TEAM, "--task", "lens-coverage", "--add", 35000,
        "--by", "cov", "--model", "glm-5.4")

    integration = run("integrate", "--team", TEAM, "--lead-token",
                      lead_token, "--check", "./check.sh", "--apply")
    (OUT / "integration.txt").write_text(integration, encoding="utf-8")

    status = run("status", "--team", TEAM, "--json")
    (OUT / "status.json").write_text(status, encoding="utf-8")
    metrics = run("metrics", "--team", TEAM)
    (OUT / "metrics.json").write_text(metrics, encoding="utf-8")
    report_path = run("report", "--team", TEAM, "--out",
                      str(OUT / "report.html"))
    (OUT / "report-path.txt").write_text(report_path, encoding="utf-8")
    shutil.copy2(ROOT / "tasks.json", OUT / "tasks.json")
    shutil.copytree(ROOT / ".agent-teams" / TEAM, OUT / "team-state",
                    ignore=shutil.ignore_patterns("workspace"))

    print("=== integration ===")
    print(integration)
    print("=== metrics ===")
    print(metrics)
    print("=== applied files in project ===")
    print(sorted(p.name for p in (ROOT / "reports").iterdir()))


if __name__ == "__main__":
    main()
