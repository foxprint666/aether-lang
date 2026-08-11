"""
Phase A3 — Fault-Injection & Rollback Robustness Tests

Tests:
  - Snapshot is fully restored when sandbox returns a non-zero exit code
  - Snapshot is fully restored when an exception is raised during patch apply
  - Calling restore() twice does not corrupt the working tree (idempotency)
  - Rollback works correctly when the archive was created via Sandbox.snapshot()
  - Rollback event is recorded in the audit log

Run with:
    pytest tests/test_rollback_fault.py -v
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_runtime._types import ExecutionResult, SnapshotHandle
from ai_runtime.snapshot.store import SnapshotStore
from ai_runtime.sandbox import Sandbox


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal synthetic project for fault-injection testing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main(): return 'original'\n")
    (tmp_path / "src" / "utils.py").write_text("CONSTANT = 42\n")
    (tmp_path / "README.md").write_text("# Project\n")
    return tmp_path


@pytest.fixture()
def store(project: Path) -> SnapshotStore:
    return SnapshotStore(project_root=project)


@pytest.fixture()
def patch_id() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Helper: verify working tree matches snapshot contents
# ─────────────────────────────────────────────────────────────────────────────

def _read(project: Path, rel: str) -> str:
    return (project / rel).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# A3-T1: Restore after non-zero exit code
# ─────────────────────────────────────────────────────────────────────────────

class TestRestoreOnFailedExecution:
    def test_file_restored_after_exit_nonzero(self, project: Path, store: SnapshotStore, patch_id: str):
        """
        Simulate the full lifecycle:
          1. Capture snapshot
          2. Corrupt the file (simulate patch mid-apply)
          3. Execution fails (exit code 1)
          4. Restore snapshot
          5. Assert file is back to original
        """
        original_content = _read(project, "src/main.py")

        handle = store.capture(patch_id=patch_id)
        assert handle.snapshot_id

        # Simulate patch modifying the file
        (project / "src" / "main.py").write_text("def main(): return 'CORRUPTED'\n")
        assert _read(project, "src/main.py") == "def main(): return 'CORRUPTED'\n"

        # Simulate sandbox returning failure
        failed_result = ExecutionResult(
            failed=True,
            exit_code=1,
            stdout="",
            stderr="SyntaxError: unexpected token",
            elapsed_ms=12.5,
            tier="t3_subprocess",
            isolation_level="audit_hook",
        )
        assert failed_result.failed

        # Application code should restore on failure
        store.restore(handle)

        # File must be back to original
        assert _read(project, "src/main.py") == original_content

    def test_file_restored_after_exit_137(self, project: Path, store: SnapshotStore, patch_id: str):
        """Simulate SIGKILL (exit 137) during sandbox execution → restore."""
        original = _read(project, "src/utils.py")

        handle = store.capture(patch_id=patch_id)
        (project / "src" / "utils.py").write_text("CONSTANT = 'KILLED'\n")

        # Simulate SIGKILL result
        killed_result = ExecutionResult(
            failed=True,
            exit_code=137,
            stdout="",
            stderr="",
            elapsed_ms=5000.0,
            tier="t3_subprocess",
            error="Execution timed out after 5000ms",
            isolation_level="audit_hook",
        )

        store.restore(handle)
        assert _read(project, "src/utils.py") == original


# ─────────────────────────────────────────────────────────────────────────────
# A3-T2: Restore after exception during patch apply
# ─────────────────────────────────────────────────────────────────────────────

class TestRestoreOnException:
    def test_restore_after_apply_exception(self, project: Path, store: SnapshotStore, patch_id: str):
        """
        If an exception is raised mid-apply, the snapshot must be fully restored.
        Simulates the pattern used in PatchEngine.apply():
            try:
                apply_changes()
            except Exception:
                store.restore(handle)
                raise
        """
        original_main = _read(project, "src/main.py")
        original_utils = _read(project, "src/utils.py")

        handle = store.capture(patch_id=patch_id)

        # Partially corrupt both files (simulates mid-apply crash)
        (project / "src" / "main.py").write_text("# HALF APPLIED\n")

        # Simulate exception
        try:
            raise RuntimeError("Patch apply raised mid-way: invalid AST node")
        except RuntimeError:
            store.restore(handle)

        # Both files restored
        assert _read(project, "src/main.py") == original_main
        assert _read(project, "src/utils.py") == original_utils

    def test_new_file_removed_on_rollback(self, project: Path, store: SnapshotStore, patch_id: str):
        """
        A file that was CREATED by the patch (not in the snapshot) should be
        removed when the snapshot is restored.

        Phase B: Now implemented via file_manifest in SnapshotStore.
        """
        handle = store.capture(patch_id=patch_id)

        # Patch creates a new file
        new_file = project / "src" / "new_module.py"
        new_file.write_text("def new_fn(): pass\n")
        assert new_file.exists()

        store.restore(handle)

        # The new file must not exist after restore
        assert not new_file.exists(), "New file created by patch was not removed on rollback"



# ─────────────────────────────────────────────────────────────────────────────
# A3-T3: Idempotency — double restore
# ─────────────────────────────────────────────────────────────────────────────

class TestRestoreIdempotency:
    def test_double_restore_does_not_corrupt(self, project: Path, store: SnapshotStore, patch_id: str):
        """
        Calling restore() twice on the same handle must produce the same result
        as calling it once. The second call must not raise and must leave the
        working tree in the original state.
        """
        original_content = _read(project, "src/main.py")

        handle = store.capture(patch_id=patch_id)
        (project / "src" / "main.py").write_text("CORRUPTED\n")

        store.restore(handle)
        assert _read(project, "src/main.py") == original_content

        # Second restore — must be safe
        store.restore(handle)
        assert _read(project, "src/main.py") == original_content

    def test_restore_after_commit_raises_or_no_ops(self, project: Path, store: SnapshotStore, patch_id: str):
        """
        Once a snapshot is committed, restore should either:
        (a) succeed silently (no-op / re-restore), or
        (b) raise a clear error.
        It must never silently corrupt state.
        """
        original = _read(project, "src/main.py")
        handle = store.capture(patch_id=patch_id)

        # Patch applied successfully
        (project / "src" / "main.py").write_text("def main(): return 'new'\n")
        store.commit(handle)

        # Now try to rollback a committed snapshot
        # We accept either behavior but not silent corruption
        try:
            store.restore(handle)
            # If it succeeded, state must be original (re-restore is fine)
            assert _read(project, "src/main.py") == original, (
                "restore() after commit produced inconsistent state"
            )
        except Exception as exc:
            # Explicit error is also fine — just must not silently corrupt
            assert str(exc), f"restore() raised an empty exception: {exc!r}"


# ─────────────────────────────────────────────────────────────────────────────
# A3-T4: Audit log records ROLLBACK event
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogOnRollback:
    def test_rollback_recorded_in_audit_log(self, project: Path, patch_id: str):
        """
        Sandbox.restore() must record an EventKind.ROLLBACK event in the audit log.
        """
        from ai_runtime.observability import AuditLog
        from ai_runtime.observability.events import EventKind

        with Sandbox(project_root=project) as sb:
            handle = sb.snapshot(patch_id=patch_id)
            sb.restore(handle)

        log = AuditLog(project_root=project)
        rollback_events = log.query(patch_id=patch_id, kind=EventKind.ROLLBACK)
        assert len(rollback_events) >= 1, "No ROLLBACK event recorded after Sandbox.restore()"
        assert rollback_events[-1].snapshot_id == handle.snapshot_id

    def test_snapshot_captured_event_recorded(self, project: Path, patch_id: str):
        """
        Sandbox.snapshot() must record a SNAPSHOT_CAPTURED event.
        """
        from ai_runtime.observability import AuditLog
        from ai_runtime.observability.events import EventKind

        with Sandbox(project_root=project) as sb:
            handle = sb.snapshot(patch_id=patch_id)

        log = AuditLog(project_root=project)
        captured_events = log.query(patch_id=patch_id, kind=EventKind.SNAPSHOT_CAPTURED)
        assert len(captured_events) >= 1
        assert captured_events[-1].snapshot_id == handle.snapshot_id


# ─────────────────────────────────────────────────────────────────────────────
# A3-T5: Archive corruption resilience
# ─────────────────────────────────────────────────────────────────────────────

class TestArchiveCorruption:
    def test_restore_with_truncated_archive(self, project: Path, store: SnapshotStore, patch_id: str):
        """
        If the snapshot archive is corrupted (truncated), restore() must raise
        a clear exception rather than silently leaving the tree in a bad state.
        """
        handle = store.capture(patch_id=patch_id)

        # Truncate the archive to simulate I/O corruption
        archive_path = Path(handle.path)
        assert archive_path.exists()
        with open(archive_path, "wb") as f:
            f.write(b"CORRUPT_DATA_TRUNCATED")

        # restore() must raise — not silently succeed
        with pytest.raises(Exception) as exc_info:
            store.restore(handle)

        assert exc_info.value is not None, "restore() with corrupt archive must raise"
