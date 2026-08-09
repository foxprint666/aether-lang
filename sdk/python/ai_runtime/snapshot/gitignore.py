"""
ai_runtime.snapshot.gitignore
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Collects the list of files to include in a snapshot archive.

Strategy:
  1. Parse .gitignore and .ai_runtimeignore in project_root.
  2. Always exclude a hardcoded deny-list (node_modules, venv, __pycache__, etc.)
  3. Walk the tree and yield only non-excluded, non-binary source files.

Relies on the `pathspec` library (already a project dependency) which
implements gitignore-style pattern matching spec-for-spec.

Performance target: file enumeration for a 10k-file project < 50ms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pathspec

# ─────────────────────────────────────────────────────────────────────────────
# Hardcoded deny-list — always excluded regardless of .gitignore contents.
# These are heavy, reproducible directories that must never be snapshotted.
#
# Notes on pathspec spectype:
#   'gitwildmatch' is deprecated in pathspec >= 0.12; use 'gitignore'.
#   We also keep bare names (without trailing slash) so directory entries
#   match under both spectype behaviours.
# ─────────────────────────────────────────────────────────────────────────────

_ALWAYS_EXCLUDE: list[str] = [
    # JS / package managers
    "node_modules",
    "node_modules/",
    ".npm",
    ".yarn",
    # Python virtualenvs
    "venv",
    "venv/",
    ".venv",
    ".venv/",
    "env",
    "env/",
    ".env",
    ".env/",
    # Version control
    ".git",
    ".git/",
    ".hg",
    ".hg/",
    ".svn",
    ".svn/",
    # Python cache
    "__pycache__",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".mypy_cache",
    ".mypy_cache/",
    ".ruff_cache",
    ".ruff_cache/",
    ".pytest_cache",
    ".pytest_cache/",
    ".tox",
    ".tox/",
    # Build artefacts
    "dist",
    "dist/",
    "build",
    "build/",
    "*.egg-info",
    "*.egg-info/",
    # Rust
    "target",
    "target/",
    ".cargo",
    ".cargo/",
    # Native objects
    "*.o",
    "*.obj",
    "*.lib",
    "*.dll",
    "*.so",
    "*.dylib",
    # Our own runtime store — NEVER snapshot snapshots
    ".ai_runtime",
    ".ai_runtime/",
]

# Use 'gitignore' spectype (replaces deprecated 'gitwildmatch' in pathspec ≥ 0.12)
try:
    _ALWAYS_SPEC = pathspec.PathSpec.from_lines("gitignore", _ALWAYS_EXCLUDE)
except ValueError:
    # pathspec < 0.12 fallback
    _ALWAYS_SPEC = pathspec.PathSpec.from_lines("gitwildmatch", _ALWAYS_EXCLUDE)  # type: ignore[arg-type]

# Max individual file size to include in archive (default 5 MB)
_MAX_FILE_BYTES = 5 * 1024 * 1024

# Directory names to skip entirely without even checking the spec
# (fast O(1) set lookup before the regex engine runs)
_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "node_modules", ".git", ".hg", ".svn",
    "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox",
    "dist", "build", "target", ".cargo", ".ai_runtime",
})


def collect_source_files(
    project_root: Path,
    max_file_bytes: int = _MAX_FILE_BYTES,
) -> Iterator[Path]:
    """
    Yield all source files under `project_root` that should be archived.

    Applies (in order):
      1. Hardcoded deny-list (fast O(1) dir-name check + pathspec fallback)
      2. .gitignore patterns from project_root
      3. .ai_runtimeignore patterns from project_root
      4. File size ceiling (skip files > max_file_bytes)

    Args:
        project_root: Absolute project directory.
        max_file_bytes: Skip individual files larger than this.

    Yields:
        Absolute Path objects for files to include.
    """
    user_spec = _load_user_spec(project_root)

    for path in _walk(project_root):
        rel = path.relative_to(project_root).as_posix()

        # Deny-list check (pathspec pass)
        if _ALWAYS_SPEC.match_file(rel):
            continue

        # User gitignore check
        if user_spec and user_spec.match_file(rel):
            continue

        # Size check
        try:
            if path.stat().st_size > max_file_bytes:
                continue
        except OSError:
            continue

        yield path


def _walk(root: Path) -> Iterator[Path]:
    """
    Recursively walk, applying two-tier directory exclusion:
      Tier 1: O(1) frozenset lookup on directory name alone (fast path)
      Tier 2: pathspec regex match as fallback
    """
    try:
        entries = list(root.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if entry.is_dir():
            # Tier 1 — fast set lookup
            if entry.name in _SKIP_DIR_NAMES:
                continue
            # Tier 2 — pathspec fallback for patterns like *.egg-info/
            if _ALWAYS_SPEC.match_file(entry.name + "/"):
                continue
            yield from _walk(entry)
        elif entry.is_file():
            yield entry


def _load_user_spec(project_root: Path):
    """Load .gitignore and .ai_runtimeignore, return combined PathSpec or None."""
    patterns: list[str] = []

    for ignore_file in (".gitignore", ".ai_runtimeignore"):
        p = project_root / ignore_file
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                patterns.extend(lines)
            except OSError:
                pass

    if not patterns:
        return None

    try:
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except ValueError:
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)  # type: ignore[arg-type]
