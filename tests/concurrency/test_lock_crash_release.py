"""A killed holder must not keep the lock: the OS releases it."""
from __future__ import annotations

import os
import signal
import sys
import time

import pytest


def _holder(team_dir: str, ready_path: str, stop_path: str) -> None:  # pragma: no cover - subprocess
    from pathlib import Path

    from agent_teams.locking import StateLock

    with StateLock(Path(team_dir), "doomed", "stuck"):
        Path(ready_path).write_text("held", encoding="utf-8")
        while not Path(stop_path).exists():
            time.sleep(0.05)


def test_lock_released_after_kill(tmp_path, spawn_ctx):
    if sys.platform == "win32":
        pytest.skip("SIGKILL semantics differ on Windows; covered by terminate test")
    ready, stop = tmp_path / "ready", tmp_path / "stop"
    p = spawn_ctx.Process(target=_holder, args=(str(tmp_path), str(ready), str(stop)))
    p.start()
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists()

    os.kill(p.pid, signal.SIGKILL)
    p.join(timeout=10)

    from pathlib import Path

    from agent_teams.locking import StateLock

    with StateLock(Path(tmp_path), "parent", "reclaim", timeout_s=10.0) as lock:
        assert lock.holder()["owner"] == "parent"


def test_lock_released_after_terminate(tmp_path, spawn_ctx):
    ready, stop = tmp_path / "ready2", tmp_path / "stop2"
    p = spawn_ctx.Process(target=_holder, args=(str(tmp_path), str(ready), str(stop)))
    p.start()
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists()

    p.terminate()
    p.join(timeout=10)

    from pathlib import Path

    from agent_teams.locking import StateLock

    with StateLock(Path(tmp_path), "parent", "reclaim", timeout_s=10.0) as lock:
        assert lock.holder()["owner"] == "parent"
