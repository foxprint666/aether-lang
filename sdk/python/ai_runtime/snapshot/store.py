"""
ai_runtime.snapshot.store
~~~~~~~~~~~~~~~~~~~~~~~~~~
SnapshotStore — the core snapshot/restore engine.

Architecture:
  ┌─────────────────────────────────────┐
  │   SnapshotStore(project_root)       │
  │                                     │
  │   .capture(patch_id)                │  Write lock → tarball → SQLite
  │       → SnapshotHandle              │
  │                                     │
  │   .restore(handle)                  │  Write lock → extract → SQLite update
  │                                     │
  │   .commit(handle)                   │  Mark status='committed' in SQLite
  │                                     │
  │   .list_snapshots()                 │  Read SQLite (no lock needed)
  │                                     │
  │   .prune(keep=10)                   │  Delete old archives + SQLite rows
  └─────────────────────────────────────┘

Storage layout (under project_root/.ai_runtime/):
  snapshots/                  ← tar.gz archives
      <snapshot_id>.tar.gz
  snapshots.db                ← SQLite index (WAL mode)
  snapshot.lock               ← advisory write lock file

SQLite WAL mode:
  WAL (Write-Ahead Log) allows concurrent readers while a write is in progress.
  Combined with the advisory file lock, this ensures snapshot_id is the
  canonical truth and no two agents corrupt the same archive simultaneously.

Snapshot target SLO (from architecture doc):
  - Capture time  < 100ms for projects < 50MB (source files only)
  - Archive size  < 10% of raw source size (gzip compression)
  - Restore time  < 500ms
"""

from __future__ import annotations

import os
import sqlite3
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from .gitignore import collect_source_files
from .lock import project_write_lock
from .._types import SnapshotHandle

# ─────────────────────────────────────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id                  TEXT PRIMARY KEY,
    patch_id            TEXT NOT NULL,
    project_root        TEXT NOT NULL,
    archive_path        TEXT NOT NULL,
    created_at          REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    archive_size_bytes  INTEGER NOT NULL DEFAULT 0,
    file_count          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snapshots_patch_id
    ON snapshots (patch_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_created_at
    ON snapshots (created_at DESC);
"""


# ─────────────────────────────────────────────────────────────────────────────
# SnapshotStore
# ─────────────────────────────────────────────────────────────────────────────

class SnapshotStore:
    """
    Filesystem snapshot engine for the AI-Safe runtime.

    One instance per project root. Thread-safe via file-level advisory locking.
    Multiple SnapshotStore instances (or even processes) pointing at the same
    project_root will correctly serialize write operations.

    Args:
        project_root: Absolute (or resolvable) path to the project directory.
        store_subdir:  Sub-directory for all runtime data (default '.ai_runtime').
    """

    def __init__(
        self,
        project_root: str | Path,
        store_subdir: str = ".ai_runtime",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._store_dir   = self.project_root / store_subdir
        self._archive_dir = self._store_dir / "snapshots"
        self._db_path     = self._store_dir / "snapshots.db"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Public API ─────────────────────────────────────────────────────────

    def capture(self, patch_id: str = "") -> SnapshotHandle:
        """
        Capture the current project state into a compressed archive.

        Steps (under write lock):
          1. Collect source files (gitignore-aware)
          2. Write to a temporary tar.gz, then atomic-rename into archive_dir
          3. Record in SQLite with status='pending'

        Args:
            patch_id: ID of the patch this snapshot is being taken for.

        Returns:
            SnapshotHandle with path pointing to the archive.

        Raises:
            OSError:      If the project cannot be read or store cannot be written.
            TimeoutError: If the write lock cannot be acquired within 10s.
        """
        snap_id = str(uuid.uuid4())
        archive_path = self._archive_dir / f"{snap_id}.tar.gz"

        with project_write_lock(self.project_root):
            file_count, size_bytes = self._write_archive(archive_path)
            self._db_insert(
                snap_id=snap_id,
                patch_id=patch_id,
                archive_path=str(archive_path),
                created_at=time.time(),
                status="pending",
                size_bytes=size_bytes,
                file_count=file_count,
            )

        return SnapshotHandle(
            snapshot_id=snap_id,
            project_root=str(self.project_root),
            patch_id=patch_id,
            path=str(archive_path),
            status="pending",
            created_at=time.time(),
            archive_size_bytes=size_bytes,
        )

    def restore(self, handle: SnapshotHandle) -> None:
        """
        Restore project to the state in `handle`.

        Steps (under write lock):
          1. Verify archive exists
          2. Extract tar.gz over project_root (overwriting changed files)
          3. Update SQLite status to 'rolled_back'

        Args:
            handle: SnapshotHandle returned by a prior capture() call.

        Raises:
            FileNotFoundError: If the archive has been pruned or never written.
            TimeoutError:      If the write lock cannot be acquired.
        """
        if not handle.path or not Path(handle.path).exists():
            raise FileNotFoundError(
                f"Snapshot archive not found: {handle.path!r}. "
                "It may have been pruned or the snapshot was never fully committed."
            )

        with project_write_lock(self.project_root):
            self._extract_archive(Path(handle.path))
            self._db_update_status(handle.snapshot_id, "rolled_back")

        handle.status = "rolled_back"

    def commit(self, handle: SnapshotHandle) -> None:
        """
        Mark a snapshot as 'committed' (patch applied successfully, no rollback needed).

        Does NOT delete the archive — that is done by prune().

        Args:
            handle: SnapshotHandle to commit.
        """
        self._db_update_status(handle.snapshot_id, "committed")
        handle.status = "committed"

    def list_snapshots(self, limit: int = 50) -> list[dict]:
        """
        Return metadata for the most recent snapshots (no lock required).

        Args:
            limit: Max rows to return (default 50).

        Returns:
            List of dicts with keys: id, patch_id, status, created_at,
            archive_size_bytes, file_count, archive_path.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, patch_id, status, created_at, "
                "archive_size_bytes, file_count, archive_path "
                "FROM snapshots "
                "ORDER BY created_at DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()

        return [
            {
                "id":                  r[0],
                "patch_id":            r[1],
                "status":              r[2],
                "created_at":          r[3],
                "archive_size_bytes":  r[4],
                "file_count":          r[5],
                "archive_path":        r[6],
            }
            for r in rows
        ]

    def prune(self, keep: int = 10) -> int:
        """
        Delete old 'committed' or 'rolled_back' snapshots, keeping the `keep`
        most recent ones. Acquires write lock to prevent race with capture.

        Args:
            keep: Number of recent snapshots to retain.

        Returns:
            Number of archives deleted.
        """
        with project_write_lock(self.project_root):
            return self._prune_locked(keep)

    def load(self, snapshot_id: str) -> Optional[SnapshotHandle]:
        """
        Load a SnapshotHandle from the SQLite index by ID.

        Returns None if not found.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT id, patch_id, project_root, archive_path, "
                "created_at, status, archive_size_bytes "
                "FROM snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()

        if row is None:
            return None

        return SnapshotHandle(
            snapshot_id=row[0],
            patch_id=row[1],
            project_root=row[2],
            path=row[3],
            created_at=row[4],
            status=row[5],
            archive_size_bytes=row[6],
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _write_archive(self, dest: Path) -> tuple[int, int]:
        """
        Write a compressed tar archive of source files to `dest`.
        Uses a temp file + atomic rename so a partial write never leaves a
        corrupt archive at the final path.

        Returns:
            (file_count, compressed_size_bytes)
        """
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".tar.gz.tmp",
            dir=self._archive_dir,
        )
        os.close(tmp_fd)

        file_count = 0
        try:
            with tarfile.open(tmp_path, "w:gz", compresslevel=6) as tar:
                for src in collect_source_files(self.project_root):
                    arcname = src.relative_to(self.project_root).as_posix()
                    tar.add(str(src), arcname=arcname)
                    file_count += 1

            # Atomic rename — safe on both Windows (Python 3.3+) and Unix
            os.replace(tmp_path, str(dest))
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

        size_bytes = dest.stat().st_size if dest.exists() else 0
        return file_count, size_bytes

    def _extract_archive(self, archive: Path) -> None:
        """
        Extract `archive` over project_root.
        On Windows, individual file errors (locked files) are caught and reported
        collectively rather than aborting mid-restore.
        """
        errors: list[str] = []
        with tarfile.open(str(archive), "r:gz") as tar:
            for member in tar.getmembers():
                dest = self.project_root / member.name
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if member.isfile():
                        with tar.extractfile(member) as src_f:  # type: ignore[union-attr]
                            dest.write_bytes(src_f.read())
                except (OSError, PermissionError) as e:
                    errors.append(f"{member.name}: {e}")

        if errors:
            raise OSError(
                f"Restore completed with {len(errors)} file error(s):\n"
                + "\n".join(errors[:10])
            )

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with WAL mode and sensible defaults."""
        con = sqlite3.connect(str(self._db_path), timeout=15.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _db_insert(
        self,
        snap_id: str,
        patch_id: str,
        archive_path: str,
        created_at: float,
        status: str,
        size_bytes: int,
        file_count: int,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO snapshots "
                "(id, patch_id, project_root, archive_path, created_at, "
                " status, archive_size_bytes, file_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (snap_id, patch_id, str(self.project_root),
                 archive_path, created_at, status, size_bytes, file_count),
            )
            con.commit()

    def _db_update_status(self, snapshot_id: str, status: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE snapshots SET status=? WHERE id=?",
                (status, snapshot_id),
            )
            con.commit()

    def _prune_locked(self, keep: int) -> int:
        """Must be called while holding the write lock."""
        with self._connect() as con:
            # Find rows to delete (oldest committed/rolled_back beyond `keep`)
            rows = con.execute(
                "SELECT id, archive_path FROM snapshots "
                "WHERE status IN ('committed','rolled_back') "
                "ORDER BY created_at DESC "
                "LIMIT -1 OFFSET ?",
                (keep,),
            ).fetchall()

            deleted = 0
            for snap_id, archive_path in rows:
                try:
                    Path(archive_path).unlink(missing_ok=True)
                except OSError:
                    pass
                con.execute("DELETE FROM snapshots WHERE id=?", (snap_id,))
                deleted += 1

            con.commit()
        return deleted
