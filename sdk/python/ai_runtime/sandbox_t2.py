"""
ai_runtime.sandbox_t2
~~~~~~~~~~~~~~~~~~~~~
Tier 2 (T2) Wasm Sandbox implementation using Wasmtime.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
import zipfile
import io
from pathlib import Path
from typing import Optional

from ._types import ExecutionResult

# A known working WASM release URL.
# If this is a zip, we extract `python.wasm`.
DEFAULT_PYTHON_WASM_URL = "https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.14.7/python-3.14.7-wasi_sdk-24.zip"


class T2WasmSandbox:
    """
    Tier 2 Sandbox executing Python via WASM/WASI using Wasmtime.
    Provides memory and CPU isolation.
    """

    def __init__(
        self,
        working_dir: str | Path,
        wasm_url: str = DEFAULT_PYTHON_WASM_URL,
        wasm_hash: Optional[str] = None,
        memory_limit_mb: int = 128,
        timeout_ms: int = 5000,
        cache_dir: Optional[str | Path] = None,
    ) -> None:
        self.working_dir = Path(working_dir).resolve()
        self.memory_limit_mb = memory_limit_mb
        self.timeout_ms = timeout_ms
        self.wasm_url = wasm_url
        self.wasm_hash = wasm_hash

        if cache_dir is None:
            # Default to .ai_runtime/cache in project root
            self.cache_dir = self.working_dir / ".ai_runtime" / "cache"
        else:
            self.cache_dir = Path(cache_dir).resolve()

        self.python_wasm_path = self.cache_dir / "python.wasm"

    def _ensure_wasm_binary(self) -> None:
        """Download and verify the WASM binary lazily."""
        if self.python_wasm_path.exists():
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"Downloading python.wasm from {self.wasm_url}...")

        req = urllib.request.Request(self.wasm_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read()

            if self.wasm_hash:
                actual_hash = hashlib.sha256(data).hexdigest()
                if actual_hash != self.wasm_hash:
                    raise ValueError(f"Hash mismatch. Expected {self.wasm_hash}, got {actual_hash}")

            if self.wasm_url.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    z.extractall(self.cache_dir)

                if not self.python_wasm_path.exists():
                    raise ValueError("python.wasm not found in the downloaded zip.")
            else:
                with open(self.python_wasm_path, "wb") as f:
                    f.write(data)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch python.wasm: {e}")

    def run(
        self,
        payload: str,
        timeout_ms: int = 5000,
        memory_limit_mb: int = 128,
        allow_network: bool = False,
        allow_filesystem: bool = False,
        cwd: Optional[Path] = None,
    ) -> ExecutionResult:
        """Execute a Python string in the WASM sandbox."""
        import wasmtime

        self.timeout_ms = timeout_ms
        self.memory_limit_mb = memory_limit_mb
        # Override cwd if provided, else use working_dir
        actual_cwd = cwd.resolve() if cwd else self.working_dir

        self._ensure_wasm_binary()

        # Engine configuration
        config = wasmtime.Config()
        config.epoch_interruption = True
        config.cache = True

        engine = wasmtime.Engine(config)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()

        # Compile module
        module = wasmtime.Module.from_file(engine, str(self.python_wasm_path))

        # Setup WASI config
        wasi_config = wasmtime.WasiConfig()

        # Pass the code as a direct string execution using -c
        wasi_config.argv = ["python", "-c", payload]
        wasi_config.env = [
            ("PYTHONHOME", "/usr/local"),
            ("PYTHONPATH", "/usr/local/lib/python3.14"),
        ]

        # We need to map the standard library
        lib_dir = self.cache_dir / "lib"
        if lib_dir.exists():
            wasi_config.preopen_dir(str(lib_dir), "/usr/local/lib")

        # Block network and filesystem by mapping only the working_dir if allow_filesystem is true
        if allow_filesystem:
            wasi_config.preopen_dir(str(actual_cwd), "/")

        # Capture stdout/stderr to temporary files
        stdout_path = self.cache_dir / f"stdout_{time.time()}.log"
        stderr_path = self.cache_dir / f"stderr_{time.time()}.log"
        wasi_config.stdout_file = str(stdout_path)
        wasi_config.stderr_file = str(stderr_path)

        store = wasmtime.Store(engine)
        store.set_wasi(wasi_config)

        # Use epoch interruption for wall-clock timeouts.
        # The store's deadline is 1 epoch tick from now.
        store.set_epoch_deadline(1)

        # memory_size limits the total memory usage
        store.set_limits(memory_size=self.memory_limit_mb * 1024 * 1024)

        try:
            instance = linker.instantiate(store, module)
            start = instance.exports(store)["_start"]
        except Exception as e:
            return ExecutionResult(
                failed=True,
                exit_code=1,
                stdout="",
                stderr=str(e),
                error=f"Instantiation error: {e}",
                elapsed_ms=0,
                tier="t2_wasm",
                isolation_level="wasm_sandbox",
            )

        t0 = time.time()
        exit_code = 0
        error_msg = None

        import threading

        def _ticker() -> None:
            time.sleep(self.timeout_ms / 1000.0)
            engine.increment_epoch()

        ticker_thread = threading.Thread(target=_ticker, daemon=True)
        ticker_thread.start()

        try:
            start(store)
        except Exception as trap:
            if type(trap).__name__ == "ExitTrap" and hasattr(trap, "code"):
                exit_code = trap.code
            elif hasattr(trap, "wasi_exit_code") and trap.wasi_exit_code() is not None:
                exit_code = trap.wasi_exit_code()
            elif hasattr(trap, "trap_code") and getattr(trap.trap_code, "name", "") == "INTERRUPT":
                error_msg = f"Timeout: execution exceeded {self.timeout_ms}ms wall-clock limit"
                exit_code = 137
            else:
                exit_code = 1
                error_msg = str(trap)

        elapsed_ms = (time.time() - t0) * 1000

        # Read outputs
        stdout = ""
        stderr = ""
        if stdout_path.exists():
            with open(stdout_path, "r", encoding="utf-8") as f:
                stdout = f.read()
            try:
                stdout_path.unlink()
            except OSError:
                pass
        if stderr_path.exists():
            with open(stderr_path, "r", encoding="utf-8") as f:
                stderr = f.read()
            try:
                stderr_path.unlink()
            except OSError:
                pass

        if error_msg is None and stderr.strip():
            # If we had stderr, let's treat it as an error string, but only if exit_code != 0
            if exit_code != 0:
                error_msg = stderr.strip()

        return ExecutionResult(
            failed=(exit_code != 0 or bool(error_msg)),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error=error_msg,
            elapsed_ms=elapsed_ms,
            tier="t2_wasm",
            isolation_level="wasm_sandbox",
        )
