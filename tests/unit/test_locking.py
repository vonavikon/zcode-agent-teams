"""Unit tests for agent_teams.locking."""
from __future__ import annotations

import json
import multiprocessing as mp
import time

import pytest

from agent_teams import locking
from agent_teams.errors import LockTimeoutError, StateRejected


def _child_hold(team_dir: str, hold_s: float, out) -> None:  # pragma: no cover - subprocess
    lock = locking.StateLock(__import__("pathlib").Path(team_dir), "child-proc", "hold")
    with lock:
        out.put(lock.token)
        time.sleep(hold_s)


class TestAcquireRelease:
    def test_acquire_and_release(self, tmp_path):
        lock = locking.StateLock(tmp_path, "lead", "init")
        with lock:
            assert lock.token
            assert lock.acquired_at is not None
            holder = lock.holder()
            assert holder["owner"] == "lead"
            assert holder["operation"] == "init"
            assert holder["pid"] == __import__("os").getpid()
        # Release clears our own record but never removes the file.
        assert locking.read_holder(tmp_path) is None
        assert (tmp_path / locking.LOCK_RELPATH).exists()

    def test_lock_file_never_unlinked(self, tmp_path):
        for i in range(3):
            with locking.StateLock(tmp_path, f"a{i}", "op"):
                pass
        assert (tmp_path / locking.LOCK_RELPATH).exists()

    def test_tokens_differ(self, tmp_path):
        tokens = []
        for _ in range(2):
            with locking.StateLock(tmp_path, "lead", "op") as lock:
                tokens.append(lock.token)
        assert tokens[0] != tokens[1]

    def test_exception_propagates_and_releases(self, tmp_path):
        with pytest.raises(RuntimeError, match="boom"):
            with locking.StateLock(tmp_path, "lead", "op"):
                raise RuntimeError("boom")
        with locking.StateLock(tmp_path, "lead", "op2"):  # releasable again
            pass

    def test_release_keeps_foreign_record(self, tmp_path):
        import sys

        lock = locking.StateLock(tmp_path, "a", "op")
        with lock:
            if sys.platform == "win32":
                # A separate handle cannot write into the locked region on
                # Windows; the foreign-record path is POSIX-only.
                return
            # Simulate a foreign writer having replaced the record while we
            # hold the lock (a stale writer bypassing the OS lock).
            (tmp_path / locking.LOCK_RELPATH).write_text(
                json.dumps({"token": "other", "owner": "ghost"}) + "\n",
                encoding="utf-8")
        raw = (tmp_path / locking.LOCK_RELPATH).read_text(encoding="utf-8")
        assert "ghost" in raw  # not cleared by a's release: token mismatch
        assert (tmp_path / locking.LOCK_RELPATH).exists()


class TestReentrancy:
    def test_same_process_double_acquire_fails_fast(self, tmp_path):
        with locking.StateLock(tmp_path, "lead", "outer"):
            start = time.monotonic()
            with pytest.raises(StateRejected) as excinfo:
                with locking.StateLock(tmp_path, "lead", "inner"):
                    pass
            assert time.monotonic() - start < 1.0
            assert excinfo.value.code == 2

    def test_registry_cleaned_after_failure(self, tmp_path):
        with pytest.raises(RuntimeError):
            with locking.StateLock(tmp_path, "lead", "op"):
                raise RuntimeError("x")
        with locking.StateLock(tmp_path, "lead", "op"):
            pass


class TestReadHolder:
    def test_missing_file(self, tmp_path):
        assert locking.read_holder(tmp_path) is None

    def test_empty_file(self, tmp_path):
        p = tmp_path / locking.LOCK_RELPATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        assert locking.read_holder(tmp_path) is None

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / locking.LOCK_RELPATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        assert locking.read_holder(tmp_path) is None

    def test_holder_alive_own_pid(self, tmp_path):
        with locking.StateLock(tmp_path, "me", "op"):
            holder = locking.read_holder(tmp_path)
            assert locking.holder_alive(holder) is True

    def test_holder_alive_dead_pid(self, tmp_path):
        import os
        import signal
        import sys
        if sys.platform == "win32":
            pytest.skip("no reaped-pid semantics check on Windows")
        r, w = os.pipe()
        pid = os.fork() if hasattr(os, "fork") else None
        if pid == 0:  # child exits immediately and is reaped by waitpid
            os.close(r)
            os._exit(0)
        os.close(w)
        os.waitpid(pid, 0)
        holder = {"pid": pid, "hostname": __import__("socket").gethostname()}
        assert locking.holder_alive(holder) is False

    def test_holder_alive_foreign_host(self):
        assert locking.holder_alive({"pid": 1, "hostname": "elsewhere"}) is None


class TestTimeout:
    def test_timeout_with_holder_diagnostics(self, tmp_path):
        ctx = mp.get_context("spawn")
        out = ctx.Queue()
        p = ctx.Process(target=_child_hold, args=(str(tmp_path), 3.0, out))
        p.start()
        child_token = out.get(timeout=10)
        try:
            with pytest.raises(LockTimeoutError) as excinfo:
                with locking.StateLock(tmp_path, "lead", "wait", timeout_s=0.5):
                    pass
            assert excinfo.value.code == 3
            message = str(excinfo.value)
            import sys

            if sys.platform == "win32":
                # Region locks deny reads through separate handles, so the
                # holder record is legitimately unreadable there.
                assert "unreadable" in message
            else:
                assert "child-proc" in message
                assert str(p.pid) in message
                assert "Do not delete the lock file" in message
                assert excinfo.value.detail["holder"]["token"] == child_token
        finally:
            p.join(timeout=10)
        # After the holder exits, the lock is acquirable again.
        with locking.StateLock(tmp_path, "lead", "after", timeout_s=15.0):
            pass

    def test_env_override_invalid_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_TEAMS_LOCK_TIMEOUT_S", "not-a-number")
        lock = locking.StateLock(tmp_path, "lead", "op")
        assert lock.timeout_s == locking.LOCK_TIMEOUT_S

    def test_env_override_valid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_TEAMS_LOCK_TIMEOUT_S", "0.25")
        lock = locking.StateLock(tmp_path, "lead", "op")
        assert lock.timeout_s == 0.25
