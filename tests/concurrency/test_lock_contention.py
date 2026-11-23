"""Contention: N processes serialize a counter through StateLock."""
from __future__ import annotations

import json
import os


def _worker(team_dir: str, increments: int, out_path: str) -> None:  # pragma: no cover - subprocess
    from pathlib import Path

    from agent_teams.locking import StateLock

    counter = Path(out_path)
    lock = StateLock(Path(team_dir), f"worker-{os.getpid()}", "increment")
    for _ in range(increments):
        with lock:
            try:
                value = json.loads(counter.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = 0
            counter.write_text(str(value + 1), encoding="utf-8")


def test_no_lost_increments(tmp_path, spawn_ctx):
    n, m = 8, 200
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="utf-8")
    procs = [spawn_ctx.Process(target=_worker, args=(str(tmp_path), m, str(counter)))
             for _ in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert all(p.exitcode == 0 for p in procs)
    assert int(counter.read_text(encoding="utf-8")) == n * m
