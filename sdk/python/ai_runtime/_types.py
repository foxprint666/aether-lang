"""
ai_runtime._types
~~~~~~~~~~~~~~~~~
Shared data-only types for the sandbox subsystem.
Kept in a separate module to eliminate circular imports between
sandbox.py and sandbox_t3.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExecutionResult:
    """Result of a sandboxed code execution."""
    failed:     bool
    exit_code:  int
    stdout:     str
    stderr:     str
    elapsed_ms: float
    tier:       str        # "t1_cranelift" | "t2_wasm" | "t3_subprocess"
    error:      Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return not self.failed

    def __bool__(self) -> bool:
        return self.succeeded


@dataclass
class SnapshotHandle:
    """
    Reference to a pre-modification snapshot captured by SnapshotStore.

    Fields:
        snapshot_id:       UUID identifying this snapshot in snapshots.db.
        project_root:      Absolute path to the captured project root.
        patch_id:          ID of the patch this snapshot was taken for.
        path:              Filesystem path to the .tar.gz archive.
        status:            'pending' | 'committed' | 'rolled_back'
        created_at:        Unix timestamp of capture.
        archive_size_bytes: Compressed archive size in bytes (0 if unknown).
    """
    snapshot_id:        str
    project_root:       str
    patch_id:           str           = ""
    path:               Optional[str] = None
    status:             str           = "pending"
    created_at:         float         = field(default_factory=time.time)
    archive_size_bytes: int           = 0
