"""
Phase 3 test suite — Snapshot & Rollback System

Tests:
  - GitIgnore file collection (deny-list, .gitignore, .ai_runtimeignore)
  - SnapshotStore.capture() → produces valid archive in correct location
  - SnapshotStore.restore() → overwrites files with archived versions
  - SnapshotStore.commit() → status transitions
  - SnapshotStore.prune()  → deletes old archives
  - SnapshotStore.list_snapshots() / load()
  - SQLite WAL mode enabled
  - Sandbox.snapshot() / restore() integration
  - Capture SLO < 100ms for small project

Run with:
    pytest tests/test_snapshot.py -v
"""

from __future__ import annotations

import os
import sqlite3
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from ai_runtime.snapshot.store import SnapshotStore
from ai_runtime.snapshot.gitignore import collect_source_files
from ai_runtime._types import SnapshotHandle


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A small synthetic project tree for snapshot testing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main(): pass\n")
    (tmp_path / "src" / "utils.py").write_text("def helper(): return 42\n")
    (tmp_path / "README.md").write_text("# Test project\n")
    (tmp_path / "pyproject.toml").write_text("[tool.ai]\n")

    # Directories that must NOT be snapshotted
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lodash.js").write_text("module.exports={}")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00" * 50)

    return tmp_path


@pytest.fixture()
def store(project: Path) -> SnapshotStore:
    return SnapshotStore(project_root=project)


# ─────────────────────────────────────────────────────────────────────────────
# GitIgnore file collection
# ─────────────────────────────────────────────────────────────────────────────

class TestGitignoreCollection:
    def test_source_files_included(self, project: Path):
        files = list(collect_source_files(project))
        names = {f.name for f in files}
        assert "main.py" in names
        assert "utils.py" in names
        assert "README.md" in names
        assert "pyproject.toml" in names

    def test_node_modules_excluded(self, project: Path):
        files = list(collect_source_files(project))
        # Use relative paths: pytest names the tmp dir 'test_node_modules_excluded0',
        # so a substring check on the FULL absolute path gives a false positive.
        rel_paths = [f.relative_to(project).as_posix() for f in files]
        assert not any("node_modules" in p for p in rel_paths)

    def test_pycache_excluded(self, project: Path):
        files = list(collect_source_files(project))
        paths = [str(f) for f in files]
        assert not any("__pycache__" in p for p in paths)

    def test_pyc_files_excluded(self, project: Path):
        (project / "compiled.pyc").write_bytes(b"\x00" * 20)
        files = list(collect_source_files(project))
        assert not any(f.suffix == ".pyc" for f in files)

    def test_gitignore_respected(self, project: Path):
        (project / ".gitignore").write_text("*.log\nbuild/\n")
        (project / "debug.log").write_text("log content")
        (project / "build").mkdir()
        (project / "build" / "output.js").write_text("var x=1;")
        files = list(collect_source_files(project))
        names = {f.name for f in files}
        assert "debug.log" not in names
        assert "output.js" not in names

    def test_ai_runtimeignore_respected(self, project: Path):
        (project / ".ai_runtimeignore").write_text("secrets.env\n")
        (project / "secrets.env").write_text("API_KEY=hunter2")
        files = list(collect_source_files(project))
        names = {f.name for f in files}
        assert "secrets.env" not in names

    def test_ai_runtime_store_excluded(self, project: Path):
        """The .ai_runtime store dir itself must never be archived."""
        store_dir = project / ".ai_runtime"
        store_dir.mkdir(exist_ok=True)
        (store_dir / "snapshots.db").write_bytes(b"SQLite")
        files = list(collect_source_files(project))
        paths = [str(f) for f in files]
        assert not any(".ai_runtime" in p for p in paths)

    def test_large_file_excluded(self, project: Path):
        """Files > 5MB must be skipped."""
        big = project / "huge_asset.bin"
        big.write_bytes(b"x" * (6 * 1024 * 1024))
        files = list(collect_source_files(project))
        assert big not in files


# ─────────────────────────────────────────────────────────────────────────────
# SnapshotStore.capture()
# ─────────────────────────────────────────────────────────────────────────────

class TestCapture:
    def test_capture_returns_handle(self, store: SnapshotStore):
        handle = store.capture("patch-001")
        assert handle.snapshot_id
        assert handle.project_root
        assert handle.patch_id == "patch-001"
        assert handle.status == "pending"

    def test_capture_creates_archive(self, store: SnapshotStore):
        handle = store.capture()
        assert handle.path is not None
        assert Path(handle.path).exists()

    def test_archive_is_valid_tar_gz(self, store: SnapshotStore):
        handle = store.capture()
        assert tarfile.is_tarfile(handle.path)

    def test_archive_contains_source_files(self, store: SnapshotStore):
        handle = store.capture()
        with tarfile.open(handle.path, "r:gz") as tar:
            names = tar.getnames()
        assert any("main.py" in n for n in names)
        assert not any("node_modules" in n for n in names)

    def test_archive_size_bytes_populated(self, store: SnapshotStore):
        handle = store.capture()
        assert handle.archive_size_bytes > 0

    def test_capture_recorded_in_db(self, store: SnapshotStore):
        handle = store.capture("p-abc")
        loaded = store.load(handle.snapshot_id)
        assert loaded is not None
        assert loaded.patch_id == "p-abc"
        assert loaded.status == "pending"

    def test_multiple_captures_distinct_ids(self, store: SnapshotStore):
        h1 = store.capture()
        h2 = store.capture()
        assert h1.snapshot_id != h2.snapshot_id
        assert h1.path != h2.path

    def test_capture_slo_under_100ms(self, project: Path):
        """Capture SLO for a small project: < 100ms."""
        store = SnapshotStore(project_root=project)
        t0 = time.perf_counter()
        store.capture("slo-test")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"capture took {elapsed_ms:.1f}ms, SLO is <100ms"


# ─────────────────────────────────────────────────────────────────────────────
# SnapshotStore.restore()
# ─────────────────────────────────────────────────────────────────────────────

class TestRestore:
    def test_restore_overwrites_modified_file(self, project: Path, store: SnapshotStore):
        original = (project / "src" / "main.py").read_text()
        handle = store.capture()

        # Simulate AI agent modification
        (project / "src" / "main.py").write_text("def main(): broken\n")
        assert (project / "src" / "main.py").read_text() != original

        store.restore(handle)
        assert (project / "src" / "main.py").read_text() == original

    def test_restore_updates_status_to_rolled_back(self, store: SnapshotStore):
        handle = store.capture()
        store.restore(handle)
        assert handle.status == "rolled_back"

        loaded = store.load(handle.snapshot_id)
        assert loaded.status == "rolled_back"

    def test_restore_missing_archive_raises(self, store: SnapshotStore):
        handle = SnapshotHandle(
            snapshot_id=str(uuid.uuid4()),
            project_root=str(store.project_root),
            path="/nonexistent/path/ghost.tar.gz",
        )
        with pytest.raises(FileNotFoundError):
            store.restore(handle)

    def test_restore_handle_with_no_path_raises(self, store: SnapshotStore):
        handle = SnapshotHandle(
            snapshot_id=str(uuid.uuid4()),
            project_root=str(store.project_root),
        )
        with pytest.raises(FileNotFoundError):
            store.restore(handle)


# ─────────────────────────────────────────────────────────────────────────────
# SnapshotStore.commit() / list / load
# ─────────────────────────────────────────────────────────────────────────────

class TestCommitAndIndex:
    def test_commit_updates_status(self, store: SnapshotStore):
        handle = store.capture()
        store.commit(handle)
        assert handle.status == "committed"
        loaded = store.load(handle.snapshot_id)
        assert loaded.status == "committed"

    def test_list_snapshots_returns_all(self, store: SnapshotStore):
        store.capture("p1")
        store.capture("p2")
        rows = store.list_snapshots()
        assert len(rows) >= 2

    def test_list_snapshots_ordered_newest_first(self, store: SnapshotStore):
        h1 = store.capture("first")
        time.sleep(0.01)
        h2 = store.capture("second")
        rows = store.list_snapshots()
        ids = [r["id"] for r in rows]
        assert ids.index(h2.snapshot_id) < ids.index(h1.snapshot_id)

    def test_load_nonexistent_returns_none(self, store: SnapshotStore):
        result = store.load("00000000-0000-0000-0000-000000000000")
        assert result is None

    def test_sqlite_wal_mode_enabled(self, store: SnapshotStore):
        """WAL mode must be set for concurrent readers."""
        store.capture()
        con = sqlite3.connect(str(store._db_path))
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        con.close()
        assert mode == "wal"


# ─────────────────────────────────────────────────────────────────────────────
# SnapshotStore.prune()
# ─────────────────────────────────────────────────────────────────────────────

class TestPrune:
    def test_prune_deletes_old_archives(self, project: Path):
        store = SnapshotStore(project_root=project)
        handles = [store.capture() for _ in range(5)]

        # commit all so they're eligible for pruning
        for h in handles:
            store.commit(h)

        deleted = store.prune(keep=2)
        assert deleted == 3

    def test_prune_removes_archive_files(self, project: Path):
        store = SnapshotStore(project_root=project)
        handles = [store.capture() for _ in range(3)]
        for h in handles:
            store.commit(h)

        store.prune(keep=1)

        remaining = list((project / ".ai_runtime" / "snapshots").glob("*.tar.gz"))
        assert len(remaining) == 1

    def test_prune_keeps_pending_snapshots(self, project: Path):
        store = SnapshotStore(project_root=project)
        pending = store.capture()   # status=pending — never pruned
        committed = store.capture()
        store.commit(committed)

        deleted = store.prune(keep=0)
        # Only the committed one should be deleted; pending survives
        assert deleted == 1
        assert Path(pending.path).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox.snapshot() / restore() integration
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxSnapshotIntegration:
    def test_sandbox_snapshot_returns_real_handle(self, project: Path):
        from ai_runtime.sandbox import Sandbox
        with Sandbox(project_root=project) as sb:
            handle = sb.snapshot()
        assert handle.path is not None
        assert Path(handle.path).exists()

    def test_sandbox_restore_reverts_changes(self, project: Path):
        from ai_runtime.sandbox import Sandbox
        original = (project / "src" / "main.py").read_text()

        with Sandbox(project_root=project) as sb:
            handle = sb.snapshot()
            (project / "src" / "main.py").write_text("CORRUPTED")
            sb.restore(handle)

        assert (project / "src" / "main.py").read_text() == original

    def test_sandbox_commit_snapshot(self, project: Path):
        from ai_runtime.sandbox import Sandbox
        with Sandbox(project_root=project) as sb:
            handle = sb.snapshot()
            sb.commit_snapshot(handle)
        assert handle.status == "committed"
