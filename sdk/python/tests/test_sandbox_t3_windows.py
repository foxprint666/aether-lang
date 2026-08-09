"""
Windows-specific T3 subprocess sandbox tests.
Only collected on win32. Tests Windows Job Object memory limits
and CREATE_NEW_PROCESS_GROUP signal isolation.

Run with:
    pytest tests/test_sandbox_t3_windows.py -v   (Windows only)
"""

from __future__ import annotations

import sys
import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only tests", allow_module_level=True)

from ai_runtime.sandbox import Sandbox
from ai_runtime.sandbox_t3 import _apply_windows_job_memory_limit


class TestWindowsJobObjects:
    def test_job_object_apply_does_not_raise(self):
        """
        Smoke test: applying a Job Object to a harmless PID should not raise.
        Uses os.getpid() as the target — may fail gracefully if already in a Job.
        """
        import os
        result = _apply_windows_job_memory_limit(os.getpid(), 256)
        # Return value is best-effort bool; just confirm no exception
        assert isinstance(result, bool)

    def test_subprocess_spawned_with_new_process_group(self):
        """
        Verify the sandbox spawns child with CREATE_NEW_PROCESS_GROUP.
        We can't directly inspect the child's flags, so we verify execution
        still works correctly — if the flag breaks spawning we'd see an error.
        """
        with Sandbox(preferred_tier="t3_subprocess") as sb:
            result = sb.execute("import os; print(os.getpid())")
        assert result.succeeded
        # Child PID should be different from our PID
        import os
        assert str(os.getpid()) not in result.stdout.strip()

    def test_memory_limit_passed_without_crash(self):
        """T3 should enforce memory limits without crashing host or child."""
        with Sandbox(preferred_tier="t3_subprocess") as sb:
            result = sb.execute("x = [0] * 1000", memory_limit_mb=256)
        assert result.succeeded
