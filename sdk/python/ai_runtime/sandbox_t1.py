"""
ai_runtime.sandbox_t1
~~~~~~~~~~~~~~~~~~~~~
Tier 1 (T1) Cranelift JIT Sandbox.

Loads the pre-compiled Rust shared library (ae_codegen.dll / libae_codegen.so)
and invokes the C-ABI ``ae_sandbox_execute`` / ``ae_sandbox_free`` symbols via
Python's ctypes.

Memory safety contract
----------------------
* ``ae_sandbox_execute`` returns a heap-allocated, null-terminated JSON C-string.
  Ownership transfers to the caller (this module).
* We MUST call ``ae_sandbox_free`` exactly once after consuming the string.
* We use a try/finally block to guarantee the free even if JSON parsing fails.

Usage
-----
::
    from ai_runtime.sandbox_t1 import T1CraneliftSandbox

    sb = T1CraneliftSandbox()
    result = sb.run("let x = 1 + 2;")
    print(result.stdout, result.failed)
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from ._types import ExecutionResult


# ─────────────────────────────────────────────────────────────────────────────
# Library discovery
# ─────────────────────────────────────────────────────────────────────────────

def _find_lib() -> Optional[Path]:
    """Locate the compiled ae_codegen shared library.

    Search order:
    1. AE_CODEGEN_LIB environment variable (explicit override)
    2. Alongside this Python package (sdk/python/)
    3. Cargo release output (target/release/)
    """
    lib_name = "ae_codegen.dll" if sys.platform == "win32" else (
        "libae_codegen.dylib" if sys.platform == "darwin" else "libae_codegen.so"
    )

    # 1. Explicit env override
    env_path = os.environ.get("AE_CODEGEN_LIB")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # 2. Next to this package's root directory
    pkg_root = Path(__file__).parent.parent  # sdk/python/
    candidate = pkg_root / lib_name
    if candidate.exists():
        return candidate

    # 3. Cargo workspace target/release/
    workspace_root = pkg_root.parent.parent  # repo root
    cargo_candidate = workspace_root / "target" / "release" / lib_name
    if cargo_candidate.exists():
        return cargo_candidate

    return None


# ─────────────────────────────────────────────────────────────────────────────
# T1CraneliftSandbox
# ─────────────────────────────────────────────────────────────────────────────

class T1CraneliftSandbox:
    """
    Tier-1 sandbox: executes Aether source through the native Cranelift JIT.

    The Rust library is loaded once and cached for the lifetime of this object.
    Thread safety: the underlying Rust interpreter is NOT thread-safe in its
    current form.  Instantiate one ``T1CraneliftSandbox`` per thread.
    """

    def __init__(self, lib_path: Optional[str | Path] = None) -> None:
        if lib_path is not None:
            resolved = Path(lib_path).resolve()
        else:
            resolved = _find_lib()

        if resolved is None or not resolved.exists():
            raise FileNotFoundError(
                "ae_codegen shared library not found. "
                "Run `cargo build --release -p ae-codegen` first, "
                "or set AE_CODEGEN_LIB=/path/to/lib."
            )

        self._lib = ctypes.CDLL(str(resolved))
        self._setup_signatures()

    def _setup_signatures(self) -> None:
        """Declare C function signatures so ctypes performs correct marshalling."""
        # ae_sandbox_execute(src_ptr: *const c_char, src_len: usize) -> *mut c_char
        self._lib.ae_sandbox_execute.restype  = ctypes.c_char_p
        self._lib.ae_sandbox_execute.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

        # ae_sandbox_free(ptr: *mut c_char) -> void
        self._lib.ae_sandbox_free.restype  = None
        self._lib.ae_sandbox_free.argtypes = [ctypes.c_char_p]

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        payload: str,
        *,
        timeout_ms: int = 5000,
        memory_limit_mb: int = 128,
        allow_network: bool = False,
        allow_filesystem: bool = False,
        cwd: Optional[Path] = None,
    ) -> ExecutionResult:
        """
        Execute an Aether source string via the Cranelift JIT.

        Args:
            payload:          Aether source code to execute.
            timeout_ms:       Timeout in milliseconds (informational only; the
                              Rust runtime enforces this via its own mechanism).
            memory_limit_mb:  Memory limit in MB (reserved for future use).
            allow_network:    Ignored in T1 (no WASI layer; native code has no
                              network syscalls unless explicitly added).
            allow_filesystem: Ignored in T1 (the interpreter has no fs ops).
            cwd:              Ignored in T1 (reserved for future use).

        Returns:
            ExecutionResult with tier="t1_cranelift".

        Raises:
            RuntimeError: If the FFI call fails at the ctypes level (not from
                          the Rust side — Rust panics are caught internally).
        """
        t0 = time.perf_counter()
        encoded = payload.encode("utf-8", errors="replace")

        try:
            raw_ptr = self._lib.ae_sandbox_execute(encoded, len(encoded))
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                error=f"FFI call failed: {exc}",
                elapsed_ms=elapsed,
                tier="t1_cranelift",
            )

        if raw_ptr is None:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True,
                exit_code=1,
                stdout="",
                stderr="FFI returned NULL",
                error="ae_sandbox_execute returned NULL pointer",
                elapsed_ms=elapsed,
                tier="t1_cranelift",
            )

        try:
            # raw_ptr is bytes when restype=c_char_p
            json_str = raw_ptr.decode("utf-8", errors="replace")
            data = json.loads(json_str)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True,
                exit_code=1,
                stdout="",
                stderr=f"JSON parse error: {exc}",
                error=f"Could not parse FFI result: {exc}",
                elapsed_ms=elapsed,
                tier="t1_cranelift",
            )
        finally:
            # Free the Rust-owned string — MUST be called exactly once.
            # c_char_p restype means ctypes already gave us a bytes copy, so
            # we need to use the original pointer from a void* restype approach.
            # With c_char_p, ctypes copies the bytes and the pointer is gone.
            # We use ae_sandbox_free on the copied bytes address via a trick:
            # see _free_via_raw below.
            pass  # Memory management handled by _free_via_raw()

        elapsed = (time.perf_counter() - t0) * 1000
        success: bool = bool(data.get("success", False))

        return ExecutionResult(
            failed=not success,
            exit_code=0 if success else 1,
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            error=data.get("error") if not success else None,
            elapsed_ms=data.get("elapsed_ms", elapsed),
            tier=data.get("tier", "t1_cranelift"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Safe run() with proper pointer management
# ─────────────────────────────────────────────────────────────────────────────

class _SafeT1CraneliftSandbox(T1CraneliftSandbox):
    """
    Variant that uses void* for the return type to retain the raw pointer
    and explicitly call ae_sandbox_free after copying the string.

    This is the recommended class to use.  The alias ``T1CraneliftSandbox``
    below points to this implementation.
    """

    def _setup_signatures(self) -> None:
        # Use c_void_p so we keep the raw pointer for ae_sandbox_free.
        self._lib.ae_sandbox_execute.restype  = ctypes.c_void_p
        self._lib.ae_sandbox_execute.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

        self._lib.ae_sandbox_free.restype  = None
        self._lib.ae_sandbox_free.argtypes = [ctypes.c_void_p]

    def run(
        self,
        payload: str,
        *,
        timeout_ms: int = 5000,
        memory_limit_mb: int = 128,
        allow_network: bool = False,
        allow_filesystem: bool = False,
        cwd: Optional[Path] = None,
    ) -> ExecutionResult:
        t0 = time.perf_counter()
        encoded = payload.encode("utf-8", errors="replace")

        try:
            raw_ptr: Optional[int] = self._lib.ae_sandbox_execute(encoded, len(encoded))
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True, exit_code=1,
                stdout="", stderr=str(exc),
                error=f"FFI call failed: {exc}",
                elapsed_ms=elapsed, tier="t1_cranelift",
            )

        if raw_ptr is None or raw_ptr == 0:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True, exit_code=1,
                stdout="", stderr="FFI returned NULL",
                error="ae_sandbox_execute returned NULL pointer",
                elapsed_ms=elapsed, tier="t1_cranelift",
            )

        json_str = ""
        parse_err: Optional[str] = None
        try:
            # Copy bytes from Rust heap before freeing
            c_str_ptr = ctypes.cast(raw_ptr, ctypes.c_char_p)
            json_bytes = c_str_ptr.value or b"{}"
            json_str = json_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            parse_err = str(exc)
        finally:
            # Always free — even if decoding failed
            self._lib.ae_sandbox_free(raw_ptr)

        if parse_err:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True, exit_code=1,
                stdout="", stderr=parse_err,
                error=f"Could not read FFI result: {parse_err}",
                elapsed_ms=elapsed, tier="t1_cranelift",
            )

        try:
            data: dict = json.loads(json_str)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return ExecutionResult(
                failed=True, exit_code=1,
                stdout="", stderr=json_str[:500],
                error=f"JSON parse error: {exc}",
                elapsed_ms=elapsed, tier="t1_cranelift",
            )

        elapsed = (time.perf_counter() - t0) * 1000
        success: bool = bool(data.get("success", False))
        return ExecutionResult(
            failed=not success,
            exit_code=0 if success else 1,
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            error=data.get("error") if not success else None,
            elapsed_ms=data.get("elapsed_ms", elapsed),
            tier=data.get("tier", "t1_cranelift"),
        )


# Re-export the safe implementation under the public name
T1CraneliftSandbox = _SafeT1CraneliftSandbox
