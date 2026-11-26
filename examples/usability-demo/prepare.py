#!/usr/bin/env python3
"""Prepare a team for a usability session (v2.1 spec 20), then print how
to open its board.

    python3 examples/usability-demo/prepare.py /tmp/usability-demo

In the given folder it creates a small git project and a team in which
every state a reader can meet is present at once: a stage accepted by a
decision, a stage in work, one on check, one blocked after two failed
checks, one waiting in the queue, a specialist who stopped answering
and was replaced, and a budget that is spent. It takes no model and no
network: the engine is driven directly. At the end it prints the
address of the board and where the lead's token is (a file with the
owner's permissions only), so the session can start.

Nothing here is a real run: it is a stage set for watching.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / \
    "plugins/agent-teams/skills/agent-teams/scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_teams import engine  # noqa: E402
from agent_teams.storage import open_store  # noqa: E402

TEAM = "demo"
SPEC = {
    "title": "Запуск продукта: исследование и план",
    "goal": "Собрать данные о рынке, оценить риски и подготовить план запуска",
    "config": {"budget_usd": 0.01},
    "tasks": [
        {"id": "research", "title": "Собрать данные о рынке", "assignee": "analyst-1",
         "lens": "behavior", "description": "Пять конкурентов, их предложения и цены",
         "files_write": ["out/research.md"], "acceptance": ["в таблице не меньше пяти конкурентов"],
         "deps": []},
        {"id": "plan", "title": "Подготовить план запуска", "assignee": "writer-1",
         "lens": "docs", "description": "Сроки и первые шаги запуска",
         "files_write": ["out/plan.md"], "acceptance": ["в плане есть сроки и первые шаги"],
         "deps": ["research"]},
        {"id": "risks", "title": "Оценить риски", "assignee": "analyst-2",
         "lens": "robustness", "description": "Что может пойти не так и насколько это вероятно",
         "files_write": ["out/risks.md"],
         "acceptance": ["у каждого риска есть оценка вероятности"], "deps": []},
        {"id": "brand", "title": "Предложить название", "assignee": "writer-1",
         "lens": "docs", "files_write": ["out/brand.md"],
         "acceptance": ["не меньше пяти вариантов"], "deps": []},
        {"id": "site", "title": "Собрать посадочную страницу", "assignee": "engineer-1",
         "lens": "correctness", "files_write": ["site/index.html"],
         "acceptance": ["страница открывается в браузере"], "deps": ["brand"]},
    ],
}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   timeout=60)


def deliver(root: Path, claim: dict, by: str, name: str, text: str) -> None:
    workspace = Path(claim["workspace"])
    target = workspace / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-qm", name)
    engine.op_submit(root, TEAM, claim["task"], by, claim["execution_token"])


def main(folder: str) -> int:
    root = Path(folder).resolve()
    if (root / ".agent-teams").exists():
        print("that folder already holds a team: give a new folder", file=sys.stderr)
        return 1
    (root / "out").mkdir(parents=True, exist_ok=True)
    (root / "site").mkdir(exist_ok=True)
    (root / ".gitignore").write_text(".agent-teams/\nspec.json\nlead-token.txt\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "demo@example.invalid")
    git(root, "config", "user.name", "usability demo")
    (root / "out" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "README.md").write_text("Проект для показа доски.\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")

    spec = root / "spec.json"
    spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    lead = engine.op_init(root, TEAM, spec)["lead_token"]

    # 1. The market data: a failed check, a rework, and a decision of the lead.
    claim = engine.op_claim(root, TEAM, "research", "analyst-1")
    deliver(root, claim, "analyst-1", "out/research.md", "пять конкурентов, без цен\n")
    engine.op_verify(root, TEAM, "research", "fail", "нет цен у конкурентов", lead)
    deliver(root, claim, "analyst-1", "out/research.md", "пять конкурентов и их цены\n")
    engine.op_verify(root, TEAM, "research", "pass", "", lead, override_reason="демо: принят без проверки")
    # 2. A specialist who stopped answering was replaced by another.
    lost = engine.op_claim(root, TEAM, "risks", "analyst-2")
    with open_store(root / ".agent-teams" / TEAM) as store, store.transaction():
        store.update_task(TEAM, "risks", heartbeat_at="2000-01-01T00:00:00.000Z")
    engine.op_recover(root, TEAM, lead)
    claim = engine.op_claim(root, TEAM, "risks", "analyst-3")
    deliver(root, claim, "analyst-3", "out/risks.md", "риски без оценок\n")
    engine.op_verify(root, TEAM, "risks", "fail", "нет оценки вероятности", lead)
    deliver(root, claim, "analyst-3", "out/risks.md", "риски, всё ещё без оценок\n")
    engine.op_verify(root, TEAM, "risks", "fail", "по-прежнему нет оценок вероятности", lead)
    # 3. One stage on check, one in work.
    on_check = engine.op_claim(root, TEAM, "brand", "writer-1")
    deliver(root, on_check, "writer-1", "out/brand.md", "пять вариантов названия\n")
    engine.op_claim(root, TEAM, "plan", "writer-1")
    # 4. The budget is spent: new stages will not start.
    for task in ("research", "risks"):
        engine.op_tokens(root, TEAM, task, 9000, "analyst-1", "glm-5.3")

    token_file = root / "lead-token.txt"
    # The lead's token for the board: a file only its owner can read.
    with open(token_file, "w", encoding="utf-8") as handle:
        handle.write(lead + "\n")
    os.chmod(token_file, 0o600)

    print(f"the team is ready in {root}")
    print("states on the board: accepted by a decision, in work, on check, waiting for")
    print("a decision after two failed checks, in the queue; one specialist replaced; budget spent")
    print()
    print("the board the owner can act from (start it at your own machine):")
    print(f"  python3 {SCRIPTS / 'team.py'} ui --team {TEAM} --project-root {root} "
          f"--port 8797 --lead-token-file {token_file}")
    print("the same board, read-only:")
    print(f"  python3 {SCRIPTS / 'team.py'} ui --team {TEAM} --project-root {root} --port 8797")
    print(f"then open http://127.0.0.1:8797/?team={TEAM}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
