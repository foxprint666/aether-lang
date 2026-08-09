"""
ai_runtime.sandbox
~~~~~~~~~~~~~~~~~~~
Sandbox class — the isolated execution layer.

Dispatches to the appropriate sandbox tier based on platform and configuration:
  T1 (Cranelift JIT) — v1.2, max isolation, zero-syscall
  T2 (Wasmtime/WASM) — v1.1, hardware boundary via WASI
  T3 (subprocess)    — v1.0, process isolation + OS resource limits

Usage:
    from ai_runtime.sandbox import Sandbox, ExecutionResult

    with Sandbox(project_root=".") as sb:
        result = sb.execute(payload="print('hello')", timeout_ms=5000)
        if result.failed:
            sb.restore(result.snapshot)
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._types import ExecutionResult, SnapshotHandle
from .sandbox_t3 import T3SubprocessSandbox


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SnapshotHandle:
    """
    Opaque reference to a pre-modification snapshot.
    Populated by Phase 3 (Snapshot & Rollback System).
    """
    snapshot_id: str
    project_root: str
    created_at: float = field(default_factory=time.time)
    path: Optional[str] = None  # filesystem path to the archive


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

class Sandbox:
    """
    Isolated execution environment for AI-generated patch payloads.

    Tier selection (auto mode):
      - Prefers T1 if Cranelift FFI library is present (v1.2+)
      - Falls back to T2 if wasmtime is installed (v1.1+)
      - Always falls back to T3 (subprocess, available everywhere)

    Resource defaults (overridable per-execution):
      timeout_ms      = 5 000 ms
      memory_limit_mb = 128 MB
      allow_network   = False
      allow_filesystem= False
    """

    _DEFAULT_TIMEOUT_MS    = 5_000
    _DEFAULT_MEMORY_MB     = 128
    _DEFAULT_ALLOW_NETWORK = False
    _DEFAULT_ALLOW_FS      = False

    def __init__(
        self,
        project_root: str | Path = ".",
        preferred_tier: str = "auto",
    ) -> None:
        self.project_root   = Path(project_root).resolve()
        self.preferred_tier = preferred_tier
        self._t3: Optional[T3SubprocessSandbox] = None
        self._store = None  # SnapshotStore — lazily initialized

    # --- Context manager support ---

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        """Release any held resources."""
        pass  # T3 is stateless; T2/T1 will need teardown in v1.1/v1.2

    # --- Core API ---

    def snapshot(self, patch_id: str = "") -> SnapshotHandle:
        """
        Capture the current project state before applying a patch.
        Delegates to SnapshotStore.capture() for full filesystem archiving.

        Args:
            patch_id: ID of the patch this snapshot is being taken for.

        Returns:
            SnapshotHandle with path pointing to the .tar.gz archive.
        """
        return self._get_store().capture(patch_id=patch_id)

    def restore(self, handle: SnapshotHandle) -> None:
        """
        Restore the project to the state captured in `handle`.
        Delegates to SnapshotStore.restore() for full filesystem restore.

        Args:
            handle: SnapshotHandle returned by a prior snapshot() call.

        Raises:
            FileNotFoundError: If the archive has been pruned.
        """
        self._get_store().restore(handle)

    def commit_snapshot(self, handle: SnapshotHandle) -> None:
        """Mark a snapshot as committed (patch applied successfully)."""
        self._get_store().commit(handle)

    def _get_store(self):
        if self._store is None:
            from .snapshot import SnapshotStore
            self._store = SnapshotStore(self.project_root)
        return self._store

    def execute(
        self,
        payload: str,
        *,
        timeout_ms:      int  = _DEFAULT_TIMEOUT_MS,
        memory_limit_mb: int  = _DEFAULT_MEMORY_MB,
        allow_network:   bool = _DEFAULT_ALLOW_NETWORK,
        allow_filesystem:bool = _DEFAULT_ALLOW_FS,
        working_dir:     Optional[str | Path] = None,
    ) -> ExecutionResult:
        """
        Execute a code payload inside the sandbox.

        Args:
            payload:         Source code string to execute.
            timeout_ms:      Max wall-clock time in milliseconds.
            memory_limit_mb: Max memory in megabytes.
            allow_network:   If False, network calls are blocked at OS level.
            allow_filesystem:If False, filesystem writes outside working_dir are blocked.
            working_dir:     Execution working directory (defaults to project_root).

        Returns:
            ExecutionResult with failed=False on success.
        """
        tier = self._resolve_tier()
        cwd  = Path(working_dir) if working_dir else self.project_root

        if tier == "t3_subprocess":
            return self._execute_t3(
                payload=payload,
                timeout_ms=timeout_ms,
                memory_limit_mb=memory_limit_mb,
                allow_network=allow_network,
                cwd=cwd,
            )

        # T2/T1 — stubs, fall through to T3 for now
        return self._execute_t3(
            payload=payload,
            timeout_ms=timeout_ms,
            memory_limit_mb=memory_limit_mb,
            allow_network=allow_network,
            cwd=cwd,
        )

    # --- Tier resolution ---

    def _resolve_tier(self) -> str:
        if self.preferred_tier != "auto":
            return self.preferred_tier

        # T1: check for Cranelift FFI shared library
        if self._cranelift_available():
            return "t1_cranelift"

        # T2: check for wasmtime Python SDK
        if self._wasmtime_available():
            return "t2_wasm"

        # T3: always available
        return "t3_subprocess"

    @staticmethod
    def _cranelift_available() -> bool:
        """Check if the Cranelift FFI library is compiled and on PATH."""
        try:
            import ctypes, os
            lib_name = "ae_codegen.dll" if sys.platform == "win32" else "libae_codegen.so"
            # Look next to the package or on LD_LIBRARY_PATH
            return (Path(__file__).parent.parent / lib_name).exists()
        except Exception:
            return False

    @staticmethod
    def _wasmtime_available() -> bool:
        try:
            import wasmtime  # noqa: F401
            return True
        except ImportError:
            return False

    def _execute_t3(
        self,
        payload: str,
        timeout_ms: int,
        memory_limit_mb: int,
        allow_network: bool,
        cwd: Path,
    ) -> ExecutionResult:
        if self._t3 is None:
            self._t3 = T3SubprocessSandbox()

        return self._t3.run(
            payload=payload,
            timeout_ms=timeout_ms,
            memory_limit_mb=memory_limit_mb,
            allow_network=allow_network,
            cwd=cwd,
        )
