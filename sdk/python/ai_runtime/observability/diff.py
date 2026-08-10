"""
ai_runtime.observability.diff
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unified diff generation between a snapshot archive and the current filesystem.

Computes per-file diffs (added, removed, modified) and produces both:
  - Machine-readable DiffResult objects (for tests / API consumers)
  - Human-readable unified diff text (for CLI output and audit records)

Uses only stdlib: `difflib` for diff computation, `tarfile` for archive reading.
No external dependencies.
"""

from __future__ import annotations

import difflib
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .._types import SnapshotHandle


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FileDiff:
    """
    Unified diff for a single file.

    Fields:
        path:           Relative file path (POSIX format).
        status:         'modified' | 'added' | 'removed' | 'unchanged'
        unified_diff:   Full unified diff text (empty for unchanged/binary).
        insertions:     Number of lines added.
        deletions:      Number of lines removed.
        is_binary:      True if file could not be decoded as UTF-8.
    """
    path:         str
    status:       str   # 'modified' | 'added' | 'removed' | 'unchanged'
    unified_diff: str   = ""
    insertions:   int   = 0
    deletions:    int   = 0
    is_binary:    bool  = False


@dataclass
class DiffResult:
    """
    Complete diff between a snapshot archive and the current filesystem.

    Fields:
        snapshot_id:    UUID of the source snapshot.
        project_root:   Absolute project root path.
        files:          List of FileDiff objects (one per changed/added/removed file).
        total_modified: Count of 'modified' files.
        total_added:    Count of 'added' files (new files not in snapshot).
        total_removed:  Count of 'removed' files (deleted since snapshot).
        total_insertions: Total lines added across all files.
        total_deletions:  Total lines removed across all files.
    """
    snapshot_id:       str
    project_root:      str
    files:             list[FileDiff] = field(default_factory=list)
    total_modified:    int = 0
    total_added:       int = 0
    total_removed:     int = 0
    total_insertions:  int = 0
    total_deletions:   int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.files)

    @property
    def summary(self) -> str:
        """Single-line summary, git-diff style."""
        parts = []
        n = self.total_modified + self.total_added + self.total_removed
        if n == 0:
            return "No changes"
        parts.append(f"{n} file{'s' if n != 1 else ''} changed")
        if self.total_insertions:
            parts.append(f"{self.total_insertions} insertion{'s' if self.total_insertions != 1 else ''}(+)")
        if self.total_deletions:
            parts.append(f"{self.total_deletions} deletion{'s' if self.total_deletions != 1 else ''}(-)")
        return ", ".join(parts)

    def unified_text(self, max_files: int = 50) -> str:
        """
        Return all file diffs concatenated as unified diff text.
        Suitable for display in a terminal or writing to a file.
        """
        chunks: list[str] = []
        for fd in self.files[:max_files]:
            if fd.unified_diff:
                chunks.append(fd.unified_diff)
            elif fd.status == "added":
                chunks.append(f"--- /dev/null\n+++ b/{fd.path}\n(binary or empty file)\n")
            elif fd.status == "removed":
                chunks.append(f"--- a/{fd.path}\n+++ /dev/null\n(file removed)\n")
        return "\n".join(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_diff(
    handle: SnapshotHandle,
    project_root: Optional[Path] = None,
    context_lines: int = 3,
    max_file_bytes: int = 1 * 1024 * 1024,  # 1 MB per file limit for diff
) -> DiffResult:
    """
    Compute the diff between the snapshot archive and the current filesystem.

    Args:
        handle:         SnapshotHandle pointing to the .tar.gz archive.
        project_root:   Project root directory (defaults to handle.project_root).
        context_lines:  Lines of context in unified diff (default 3).
        max_file_bytes: Skip diff for individual files larger than this.

    Returns:
        DiffResult with per-file FileDiff objects and summary statistics.

    Raises:
        FileNotFoundError: If the snapshot archive does not exist.
    """
    root = Path(project_root or handle.project_root).resolve()

    if not handle.path or not Path(handle.path).exists():
        raise FileNotFoundError(
            f"Snapshot archive not found: {handle.path!r}. "
            "Archive may have been pruned."
        )

    # 1. Load all files from the archive into memory
    archived: dict[str, bytes] = _load_archive(handle.path)

    # 2. Walk current filesystem, collecting source files
    current: dict[str, bytes] = _load_current(root, max_file_bytes)

    # 3. Compare
    result = DiffResult(
        snapshot_id=handle.snapshot_id,
        project_root=str(root),
    )

    all_paths = set(archived) | set(current)
    for rel_path in sorted(all_paths):
        old_bytes = archived.get(rel_path)
        new_bytes = current.get(rel_path)

        fd = _diff_file(rel_path, old_bytes, new_bytes, context_lines)
        if fd.status != "unchanged":
            result.files.append(fd)
            result.total_insertions += fd.insertions
            result.total_deletions  += fd.deletions
            if fd.status == "modified":
                result.total_modified += 1
            elif fd.status == "added":
                result.total_added += 1
            elif fd.status == "removed":
                result.total_removed += 1

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_archive(archive_path: str) -> dict[str, bytes]:
    """Load all files from a .tar.gz archive into a {relpath: bytes} dict."""
    contents: dict[str, bytes] = {}
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                try:
                    f = tar.extractfile(member)
                    if f is not None:
                        contents[member.name] = f.read()
                except Exception:
                    pass
    return contents


def _load_current(root: Path, max_file_bytes: int) -> dict[str, bytes]:
    """
    Load all source files from the current filesystem.
    Reuses the same exclusion logic as the snapshot system.
    """
    from ..snapshot.gitignore import collect_source_files
    contents: dict[str, bytes] = {}
    for path in collect_source_files(root):
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            rel = path.relative_to(root).as_posix()
            contents[rel] = path.read_bytes()
        except OSError:
            pass
    return contents


def _diff_file(
    rel_path: str,
    old_bytes: Optional[bytes],
    new_bytes: Optional[bytes],
    context_lines: int,
) -> FileDiff:
    """Compute FileDiff for a single file."""
    # File added (exists now but not in snapshot)
    if old_bytes is None and new_bytes is not None:
        lines = _count_lines(new_bytes)
        return FileDiff(
            path=rel_path,
            status="added",
            insertions=lines,
        )

    # File removed (was in snapshot but not now)
    if old_bytes is not None and new_bytes is None:
        lines = _count_lines(old_bytes)
        return FileDiff(
            path=rel_path,
            status="removed",
            deletions=lines,
        )

    # Both exist — compare
    assert old_bytes is not None and new_bytes is not None

    if old_bytes == new_bytes:
        return FileDiff(path=rel_path, status="unchanged")

    # Try to decode as UTF-8
    try:
        old_text = old_bytes.decode("utf-8")
        new_text = new_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Binary file — report as modified without diff text
        return FileDiff(path=rel_path, status="modified", is_binary=True)

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=context_lines,
    ))

    insertions = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    deletions  = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    return FileDiff(
        path=rel_path,
        status="modified",
        unified_diff="".join(diff_lines),
        insertions=insertions,
        deletions=deletions,
    )


def _count_lines(data: bytes) -> int:
    try:
        return data.decode("utf-8").count("\n")
    except UnicodeDecodeError:
        return 0
