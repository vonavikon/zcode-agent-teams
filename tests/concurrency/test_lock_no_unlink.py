"""The lock file is never unlinked and a timing-out waiter clears nothing."""
from __future__ import annotations

import json
import time


def _holder(team_dir: str, hold_s: float, ready_path: str) -> None:  # pragma: no cover - subprocess
    from pathlib import Path

    from agent_teams.locking import StateLock

    with StateLock(Path(team_dir), "holder-a", "hold"):
        Path(ready_path).write_text("ok", encoding="utf-8")
        time.sleep(hold_s)


def test_lock_file_persists_across_processes(tmp_path, spawn_ctx):
    ready = tmp_path / "ready"
    p = spawn_ctx.Process(target=_holder, args=(str(tmp_path), 1.5, str(ready)))
    p.start()
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists()

    from agent_teams.errors import LockTimeoutError
    from agent_teams.locking import LOCK_RELPATH, StateLock

    try:
        with StateLock(tmp_path, "waiter-b", "wait", timeout_s=0.3):
            pass
        raise AssertionError("expected a timeout")
    except LockTimeoutError as exc:
        # The waiter cleared nothing. On Windows the holder record is
        # unreadable through separate handles while the lock is held.
        import sys

        if sys.platform != "win32":
            assert exc.detail["holder"]["owner"] == "holder-a"

    import sys

    record_while_held = None
    if sys.platform != "win32":
        record_while_held = json.loads(
            (tmp_path / LOCK_RELPATH).read_text(encoding="utf-8"))
    p.join(timeout=30)

    # After A finishes normally the file still exists, A's own record is
    # cleared (empty content), and nothing was unlinked at any point.
    assert (tmp_path / LOCK_RELPATH).exists()
    assert (tmp_path / LOCK_RELPATH).read_text(encoding="utf-8").strip() == ""
    if record_while_held is not None:
        assert record_while_held["owner"] == "holder-a"
