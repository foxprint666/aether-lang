"""
ai_runtime.snapshot.lock
~~~~~~~~~~~~~~~~~~~~~~~~~
Cross-platform advisory file lock for snapshot write serialization.

Concurrency model (per plan §Gap-3):
  - validate() calls are fully parallel-safe (read-only, no locking needed).
  - capture() and restore() acquire an exclusive write lock on a per-project
    lock file before touching the archive or index.
  - No two agents can checkpoint the same project simultaneously.
  - Lock is always released — even on exception — via context manager.

Implementation:
  - Unix:    fcntl.flock(fd, LOCK_EX)
  - Windows: msvcrt.locking() with a retry loop (LOCK_EX semantics)

Both platforms use the same lock file path; the lock is advisory (cooperative)
not enforced by the OS kernel against non-cooperating processes.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl


@contextmanager
def project_write_lock(
    project_root: Path,
    timeout_s: float = 10.0,
    poll_interval_s: float = 0.05,
    *,
    store_dir: Path | None = None,
) -> Generator[None, None, None]:
    """
    Context manager that acquires an exclusive write lock for the project.

    Usage:
        with project_write_lock(project_root):
            # safe to write snapshot here

    Args:
        project_root:    The project directory being snapshotted.
        timeout_s:       Max seconds to wait for lock before raising TimeoutError.
        poll_interval_s: Windows polling interval (not used on Unix).
        store_dir:       Optional runtime store directory. Defaults to
                         ``project_root/.ai_runtime``.

    Raises:
        TimeoutError: If the lock cannot be acquired within timeout_s.
        OSError:      If the lock file cannot be created.
    """
    lock_dir = store_dir if store_dir is not None else project_root / ".ai_runtime"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "snapshot.lock"

    if _IS_WINDOWS:
        with _windows_lock(lock_path, timeout_s, poll_interval_s):
            yield
    else:
        with _unix_lock(lock_path, timeout_s):
            yield


@contextmanager
def _unix_lock(
    lock_path: Path,
    timeout_s: float,
) -> Generator[None, None, None]:
    """fcntl.flock exclusive lock with timeout via non-blocking + retry."""
    deadline = time.monotonic() + timeout_s
    fd = open(lock_path, "w")
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break  # acquired
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    fd.close()
                    raise TimeoutError(
                        f"Could not acquire snapshot lock at {lock_path} "
                        f"within {timeout_s}s"
                    )
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


@contextmanager
def _windows_lock(
    lock_path: Path,
    timeout_s: float,
    poll_s: float,
) -> Generator[None, None, None]:
    """
    msvcrt.locking() exclusive lock on Windows.
    msvcrt.locking uses a 1-byte lock region; we retry until acquired.
    """
    deadline = time.monotonic() + timeout_s
    # Open for read+write, create if not exists
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o666)
    try:
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break  # acquired
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError(
                        f"Could not acquire snapshot lock at {lock_path} "
                        f"within {timeout_s}s"
                    )
                time.sleep(poll_s)
        yield
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
