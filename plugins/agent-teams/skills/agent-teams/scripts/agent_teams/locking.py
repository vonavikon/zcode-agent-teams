"""Inter-process state lock for one team directory.

The v0.2 engine used an O_EXCL lock file with mtime-based stale reclaim
and an unlink on release. That design has two races: two processes can
unlink the same stale file at once, and release can delete a lock that
already belongs to someone else. This module replaces it with OS file
locks, which the operating system releases when the holder process dies:

- POSIX: ``fcntl.flock(fd, LOCK_EX | LOCK_NB)``
- Windows: ``msvcrt.locking(fd, LK_NBLCK, 1)`` on byte 0

The lock file itself is never unlinked. A holder record (JSON line with
owner, operation, pid, hostname, token and acquisition time) is written
while the lock is held, for diagnostics only: mutual exclusion comes from
the OS lock, not from the file content.

Public API::

    StateLock(team_dir, owner, operation="", timeout_s=None)  # context manager
    read_holder(team_dir) -> dict | None
    holder_alive(holder) -> bool | None
"""
from __future__ import annotations

import json
import os
import random
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

from .errors import ExternalError, LockTimeoutError, StateRejected

LOCK_TIMEOUT_S = 10.0
LOCK_RELPATH = "locks/.state.lock"
_ENV_TIMEOUT = "AGENT_TEAMS_LOCK_TIMEOUT_S"

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    try:
        import msvcrt
    except ImportError:  # neither facility (unusual platform)
        msvcrt = None

# Same-process double acquisition fails fast instead of waiting out the
# timeout: the lock is not reentrant. Keyed by the resolved team directory.
_held_paths: set[str] = set()
_held_registry_lock = threading.Lock()


def _env_timeout() -> float:
    raw = os.environ.get(_ENV_TIMEOUT, "").strip()
    if not raw:
        return LOCK_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return LOCK_TIMEOUT_S
    return value if value > 0 else LOCK_TIMEOUT_S


def _now_iso() -> str:
    # A fixed local format is enough here; storage.now_iso stays the
    # package-wide helper for state files.
    t = time.time()
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))
    return f"{base}.{int((t % 1) * 1000):03d}Z"


def _try_lock(fd: int) -> bool:
    """Acquire the OS lock without blocking; True on success."""
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:  # Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _try_unlock(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass  # A closed or already-unlocked fd: nothing to release.


def _read_record(lock_path: Path) -> dict | None:
    """Read the holder record at an exact lock-file path; None when absent."""
    try:
        raw = Path(lock_path).read_text(encoding="utf-8").strip()
        if not raw:
            return None
        holder = json.loads(raw)
        return holder if isinstance(holder, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_holder(team_dir: Path) -> dict | None:
    """Read the holder record for a team directory without acquiring.

    On Windows this returns None while another process holds the lock:
    the region lock denies reads through separate handles. The holder
    itself reads its own record through the locking descriptor
    (StateLock.holder).
    """
    return _read_record(Path(team_dir) / LOCK_RELPATH)


def _read_record_fd(fd: int) -> dict | None:
    """Read the holder record through the locking descriptor itself.

    Region locks permit I/O through the handle that owns them, so this
    works on Windows even while the lock is held.
    """
    try:
        position = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        os.lseek(fd, position, os.SEEK_SET)
        raw = b"".join(chunks).decode("utf-8", "replace").strip()
        if not raw:
            return None
        holder = json.loads(raw)
        return holder if isinstance(holder, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def holder_alive(holder: dict | None) -> bool | None:
    """Whether the recorded pid lives on this host; None when unknowable."""
    if not holder or holder.get("hostname") != socket.gethostname():
        return None
    pid = holder.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        return None  # No cheap cross-process check in the stdlib.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Exists, but belongs to another user.
    except OSError:
        return None
    return True


class StateLock:
    """Exclusive lock over one team's state, as a context manager."""

    LOCK_RELPATH = LOCK_RELPATH

    def __init__(self, team_dir: Path, owner: str, operation: str = "",
                 timeout_s: float | None = None):
        if fcntl is None and msvcrt is None:
            raise ExternalError(
                "no OS file-lock facility (neither fcntl nor msvcrt) on this platform")
        self.path = Path(team_dir) / LOCK_RELPATH
        self.owner = owner
        self.operation = operation
        self.timeout_s = timeout_s if timeout_s is not None else _env_timeout()
        self.token = ""
        self.acquired_at: float | None = None
        self._fd: int | None = None
        self._key = str(self.path.resolve().parent) + "/" + self.path.name

    # -- context manager ------------------------------------------------

    def __enter__(self) -> "StateLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _held_registry_lock:
            if self._key in _held_paths:
                raise StateRejected(
                    f"state lock is not reentrant: {self.path} is already held "
                    "by this process (nested StateLock on the same team)")
            _held_paths.add(self._key)
        try:
            self._acquire()
        except BaseException:
            with _held_registry_lock:
                _held_paths.discard(self._key)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._release()
        finally:
            with _held_registry_lock:
                _held_paths.discard(self._key)

    # -- internals ------------------------------------------------------

    def _acquire(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        attempt = 0
        while True:
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
            if _try_lock(fd):
                self._fd = fd
                self.token = secrets.token_hex(8)
                self.acquired_at = time.monotonic()
                self._write_holder()
                return
            os.close(fd)
            if time.monotonic() >= deadline:
                self._raise_timeout(_read_record(self.path))
            time.sleep(min(0.02 * 2 ** attempt, 0.4) + random.uniform(0.0, 0.05))
            attempt += 1

    def _write_holder(self) -> None:
        record = {
            "version": 1,
            "token": self.token,
            "owner": self.owner,
            "operation": self.operation,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": _now_iso(),
        }
        data = json.dumps(record, ensure_ascii=True) + "\n"
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.ftruncate(self._fd, 0)
        os.write(self._fd, data.encode("utf-8"))

    def _raise_timeout(self, holder: dict | None) -> None:
        where = f"lock not acquired within {self.timeout_s:.1f}s: {self.path}"
        if not holder:
            raise LockTimeoutError(
                f"{where} (holder record unreadable; the OS lock is held by "
                "another process)", detail={"lock": str(self.path)})
        age = ""
        acquired = holder.get("acquired_at")
        alive = holder_alive(holder)
        if acquired:
            try:
                from datetime import datetime, timezone
                dt = datetime.strptime(acquired, "%Y-%m-%dT%H:%M:%S.%fZ"
                                       ).replace(tzinfo=timezone.utc)
                age = f", held for {max(0.0, time.time() - dt.timestamp()):.1f}s"
            except ValueError:
                pass
        alive_txt = "unknown" if alive is None else ("yes" if alive else "no")
        raise LockTimeoutError(
            f"{where}; holder: owner={holder.get('owner')!r} "
            f"operation={holder.get('operation')!r} pid={holder.get('pid')} "
            f"host={holder.get('hostname')} alive={alive_txt}{age}. "
            "The OS releases the lock when the holder process exits. If the "
            "holder is alive and stuck, inspect or stop that process. Do not "
            "delete the lock file.",
            detail={"lock": str(self.path), "holder": holder,
                    "holder_alive": alive})

    def _release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            current = _read_record_fd(fd)
            if current and current.get("token") == self.token:
                # Still our record: clear it while holding the lock. Never
                # touch a record another process wrote after us.
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
        except OSError:
            pass
        finally:
            _try_unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass

    def holder(self) -> dict | None:
        """The holder record currently in the lock file (may be foreign)."""
        if self._fd is not None:
            # We hold the lock: read through the locking descriptor, which
            # also works on Windows (separate handles are locked out).
            return _read_record_fd(self._fd)
        return _read_record(self.path)
