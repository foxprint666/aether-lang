"""
ai_runtime.sandbox_t3
~~~~~~~~~~~~~~~~~~~~~~
T3 Subprocess Sandbox — Tier-3 execution using OS process isolation.

Platform-specific resource limits:
  - Linux/macOS: resource.setrlimit (RLIMIT_AS for memory, RLIMIT_CPU for CPU)
  - Windows:     Win32 Job Objects via ctypes.windll.kernel32
                 (SetInformationJobObject with JobObjectExtendedLimitInformation)

Isolation model:
  - Each execution spawns a fresh subprocess (no persistent worker pool)
  - subprocess.CREATE_NEW_PROCESS_GROUP on Windows for clean kill
  - os.setsid() on Unix for process group isolation
  - timeout enforced via subprocess.communicate(timeout=...)
  - Memory limits applied BEFORE exec via preexec_fn (Unix) or Job Object (Windows)

Performance target: < 500ms overhead per execution (excluding payload runtime).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from ._types import ExecutionResult

# Path to the worker script
_RUNNER_PATH = Path(__file__).parent / "sandbox_runner.py"

# ─────────────────────────────────────────────────────────────────────────────
# Platform detection
# ─────────────────────────────────────────────────────────────────────────────

_IS_WINDOWS = sys.platform == "win32"

if not _IS_WINDOWS:
    import resource as _resource   # Unix only


# ─────────────────────────────────────────────────────────────────────────────
# Windows Job Object (memory limiting without Docker)
# ─────────────────────────────────────────────────────────────────────────────

if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as _wt

    _kernel32  = ctypes.windll.kernel32
    _PROCESS_ALL_ACCESS = 0x001F0FFF
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_JOB_MEMORY     = 0x00000200

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit",   ctypes.c_int64),
            ("PerJobUserTimeLimit",        ctypes.c_int64),
            ("LimitFlags",                ctypes.c_uint32),
            ("MinimumWorkingSetSize",      ctypes.c_size_t),
            ("MaximumWorkingSetSize",      ctypes.c_size_t),
            ("ActiveProcessLimit",         ctypes.c_uint32),
            ("Affinity",                   ctypes.c_void_p),
            ("PriorityClass",             ctypes.c_uint32),
            ("SchedulingClass",           ctypes.c_uint32),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount",  ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount",   ctypes.c_uint64),
            ("WriteTransferCount",  ctypes.c_uint64),
            ("OtherTransferCount",  ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo",                _IO_COUNTERS),
            ("ProcessMemoryLimit",    ctypes.c_size_t),
            ("JobMemoryLimit",        ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed",     ctypes.c_size_t),
        ]
        
    class _JOBOBJECT_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
        _fields_ = [
            ("CompletionKey", ctypes.c_void_p),
            ("CompletionPort", ctypes.c_void_p)
        ]

    def _apply_windows_job_memory_limit(pid: int, memory_limit_mb: int) -> bool:
        """
        Attach the process (by PID) to a new Job Object with a per-process
        memory limit. Returns True on success.

        Windows Job Objects are the correct, documented mechanism for
        constraining child process memory usage without Docker.
        """
        try:
            MB = 1024 * 1024
            limit_bytes = memory_limit_mb * MB

            # Open the child process
            proc_handle = _kernel32.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
            if not proc_handle:
                return False

            # Create a new Job Object
            job = _kernel32.CreateJobObjectW(None, None)
            if not job:
                _kernel32.CloseHandle(proc_handle)
                return False

            # Configure extended limits
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_JOB_MEMORY
            )
            info.ProcessMemoryLimit = limit_bytes
            info.JobMemoryLimit     = limit_bytes

            _kernel32.SetInformationJobObject(
                job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            
            _kernel32.CreateIoCompletionPort.argtypes = [_wt.HANDLE, _wt.HANDLE, ctypes.c_void_p, _wt.DWORD]
            _kernel32.CreateIoCompletionPort.restype = _wt.HANDLE
            iocp = _kernel32.CreateIoCompletionPort(_wt.HANDLE(-1), None, 0, 1)

            assoc = _JOBOBJECT_ASSOCIATE_COMPLETION_PORT()
            assoc.CompletionKey = ctypes.c_void_p(job)
            assoc.CompletionPort = ctypes.c_void_p(iocp)
            _kernel32.SetInformationJobObject(
                job,
                7, # JobObjectAssociateCompletionPortInformation
                ctypes.byref(assoc),
                ctypes.sizeof(assoc)
            )

            # Assign process to Job Object
            _kernel32.AssignProcessToJobObject(job, proc_handle)
            
            import threading
            def watchdog():
                msg = ctypes.c_uint32()
                key = ctypes.c_void_p()
                ov = ctypes.c_void_p()
                while True:
                    res = _kernel32.GetQueuedCompletionStatus(iocp, ctypes.byref(msg), ctypes.byref(key), ctypes.byref(ov), 0xFFFFFFFF)
                    if not res:
                        break
                    if msg.value in (9, 10): # PROCESS_MEMORY_LIMIT or JOB_MEMORY_LIMIT
                        _kernel32.TerminateProcess(proc_handle, 137) # OOM exit code approximation
                        break
                    elif msg.value == 4: # ACTIVE_PROCESS_ZERO
                        break
                _kernel32.CloseHandle(proc_handle)
                _kernel32.CloseHandle(job)
                _kernel32.CloseHandle(iocp)

            t = threading.Thread(target=watchdog, daemon=True)
            t.start()
            
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# T3 Sandbox
# ─────────────────────────────────────────────────────────────────────────────

class T3SubprocessSandbox:
    """
    Tier-3 subprocess sandbox. Portable, zero extra dependencies.

    One fresh subprocess per execution — no persistent worker pool.
    Designed for safety-first; performance can be improved in v1.1+ tiers.
    """

    def run(
        self,
        payload: str,
        *,
        timeout_ms:      int,
        memory_limit_mb: int,
        allow_network:   bool,
        allow_filesystem:bool,
        cwd:             Path,
    ) -> ExecutionResult:
        t0 = time.perf_counter()

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        ) as result_file:
            result_path = result_file.name

        request_json = json.dumps({
            "payload":          payload,
            "result_path":      result_path,
            "allow_network":    allow_network,
            "allow_filesystem": allow_filesystem,
            "working_dir":      str(cwd),
        })

        timeout_sec = timeout_ms / 1000.0

        try:
            proc = self._spawn(
                request_json=request_json,
                cwd=cwd,
                memory_limit_mb=memory_limit_mb,
                allow_network=allow_network,
            )

            try:
                stdout_bytes, stderr_bytes = proc.communicate(
                    timeout=timeout_sec
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                elapsed = (time.perf_counter() - t0) * 1000
                _cleanup(result_path)
                return ExecutionResult(
                    failed=True,
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    elapsed_ms=round(elapsed, 2),
                    tier="t3_subprocess",
                    error=f"Execution timed out after {timeout_ms}ms",
                )

            elapsed = (time.perf_counter() - t0) * 1000

            # Read structured result from the result file
            result_data = _read_result(result_path)

            exit_code = proc.returncode
            failed    = exit_code != 0

            return ExecutionResult(
                failed=failed,
                exit_code=exit_code,
                stdout=result_data.get("stdout", stdout_bytes.decode("utf-8", errors="replace")),
                stderr=result_data.get("stderr", stderr_bytes.decode("utf-8", errors="replace")),
                elapsed_ms=round(elapsed, 2),
                tier="t3_subprocess",
                error=result_data.get("error") if failed else None,
            )

        except FileNotFoundError as e:
            elapsed = (time.perf_counter() - t0) * 1000
            _cleanup(result_path)
            return ExecutionResult(
                failed=True, exit_code=-2,
                stdout="", stderr="",
                elapsed_ms=round(elapsed, 2),
                tier="t3_subprocess",
                error=f"Could not spawn subprocess: {e}",
            )
        finally:
            _cleanup(result_path)

    def _spawn(
        self,
        request_json: str,
        cwd: Path,
        memory_limit_mb: int,
        allow_network: bool,
    ) -> subprocess.Popen:
        """Spawn the worker subprocess with platform-appropriate isolation."""

        python = sys.executable
        cmd    = [python, str(_RUNNER_PATH)]

        if _IS_WINDOWS:
            return self._spawn_windows(cmd, request_json, cwd, memory_limit_mb)
        else:
            return self._spawn_unix(cmd, request_json, cwd, memory_limit_mb, allow_network)

    @staticmethod
    def _spawn_windows(
        cmd: list[str],
        request_json: str,
        cwd: Path,
        memory_limit_mb: int,
    ) -> subprocess.Popen:
        """
        Windows spawn: CREATE_NEW_PROCESS_GROUP for signal isolation.
        Memory limits applied via Job Object AFTER spawn (see below).
        """
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        # Enforce memory limit via Windows Job Objects
        _apply_windows_job_memory_limit(proc.pid, memory_limit_mb)
        
        # Send payload via stdin
        proc.stdin.write(request_json.encode("utf-8"))
        proc.stdin.close()
        return proc

    @staticmethod
    def _spawn_unix(
        cmd: list[str],
        request_json: str,
        cwd: Path,
        memory_limit_mb: int,
        allow_network: bool,
    ) -> subprocess.Popen:
        """
        Unix spawn: setsid() for process group isolation + resource.setrlimit
        applied inside the child via preexec_fn BEFORE exec.

        preexec_fn runs in the child process after fork() but before exec(),
        making it the correct place to set resource limits without affecting
        the parent process.
        """
        mb = 1024 * 1024
        limit_bytes = memory_limit_mb * mb

        def _preexec() -> None:  # runs in child
            os.setsid()  # new session — child can't receive parent signals
            try:
                # Address space limit (virtual memory)
                _resource.setrlimit(
                    _resource.RLIMIT_AS,
                    (limit_bytes, limit_bytes),
                )
                # CPU time limit (hard limit = 2× soft to allow cleanup)
                _resource.setrlimit(
                    _resource.RLIMIT_CPU,
                    (30, 60),  # 30s soft / 60s hard
                )
            except Exception:
                pass  # Best-effort; don't abort the child

        if not allow_network:
            cmd = ["unshare", "-r", "-n"] + cmd

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            preexec_fn=_preexec,
        )
        proc.stdin.write(request_json.encode("utf-8"))
        proc.stdin.close()
        return proc


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_result(result_path: str) -> dict:
    try:
        with open(result_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
