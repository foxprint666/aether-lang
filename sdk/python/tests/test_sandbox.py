"""
Phase 2 test suite — Sandbox Execution Layer (platform-agnostic)
Tests that run identically on Windows and Linux.

Run with:
    pytest tests/test_sandbox.py -v
"""

from __future__ import annotations

import uuid
import time
import sys

import pytest

from ai_runtime.sandbox import Sandbox, ExecutionResult


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_sandbox(**kwargs) -> Sandbox:
    return Sandbox(project_root=".", **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Basic execution
# ─────────────────────────────────────────────────────────────────────────────

class TestT3BasicExecution:
    def test_simple_print_succeeds(self):
        with make_sandbox() as sb:
            result = sb.execute("print('hello sandbox')")
        assert result.succeeded
        assert result.exit_code == 0
        assert "hello sandbox" in result.stdout
        assert result.tier == "t3_subprocess"

    def test_arithmetic_output(self):
        with make_sandbox() as sb:
            result = sb.execute("print(6 * 7)")
        assert result.succeeded
        assert "42" in result.stdout

    def test_multiline_script(self):
        payload = (
            "total = 0\n"
            "for i in range(10):\n"
            "    total += i\n"
            "print(total)\n"
        )
        with make_sandbox() as sb:
            result = sb.execute(payload)
        assert result.succeeded
        assert "45" in result.stdout

    def test_stderr_captured(self):
        with make_sandbox() as sb:
            result = sb.execute("import sys; sys.stderr.write('err output')")
        assert result.succeeded
        assert "err output" in result.stderr

    def test_elapsed_ms_populated(self):
        with make_sandbox() as sb:
            result = sb.execute("pass")
        assert result.elapsed_ms >= 0
        assert isinstance(result.elapsed_ms, float)

    def test_context_manager_enter_exit(self):
        """Sandbox works as a context manager."""
        with Sandbox(project_root=".") as sb:
            result = sb.execute("print('ctx')")
        assert "ctx" in result.stdout

    def test_execution_result_bool_true_on_success(self):
        with make_sandbox() as sb:
            result = sb.execute("pass")
        assert bool(result) is True

    def test_execution_result_bool_false_on_failure(self):
        with make_sandbox() as sb:
            result = sb.execute("raise RuntimeError('boom')")
        assert bool(result) is False


# ─────────────────────────────────────────────────────────────────────────────
# Failure and error handling
# ─────────────────────────────────────────────────────────────────────────────

class TestT3Failures:
    def test_syntax_error_returns_failed(self):
        with make_sandbox() as sb:
            result = sb.execute("def broken(:\n    pass")
        assert result.failed
        assert result.exit_code != 0

    def test_runtime_exception_returns_failed(self):
        with make_sandbox() as sb:
            result = sb.execute("raise ValueError('test error')")
        assert result.failed
        assert result.exit_code != 0
        assert result.error is not None

    def test_zero_division_returns_failed(self):
        with make_sandbox() as sb:
            result = sb.execute("x = 1 / 0")
        assert result.failed

    def test_exit_code_nonzero_on_sys_exit(self):
        with make_sandbox() as sb:
            result = sb.execute("import sys; sys.exit(42)")
        assert result.exit_code == 42
        assert result.failed

    def test_sys_exit_zero_is_success(self):
        with make_sandbox() as sb:
            result = sb.execute("import sys; sys.exit(0)")
        assert result.succeeded
        assert result.exit_code == 0


# ─────────────────────────────────────────────────────────────────────────────
# Timeout enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestT3Timeout:
    def test_timeout_kills_infinite_loop(self):
        with make_sandbox() as sb:
            result = sb.execute(
                "while True: pass",
                timeout_ms=800,
            )
        assert result.failed
        assert "timed out" in (result.error or "").lower()

    def test_fast_script_completes_before_timeout(self):
        with make_sandbox() as sb:
            result = sb.execute("print('fast')", timeout_ms=5000)
        assert result.succeeded

    def test_timeout_elapsed_ms_is_reasonable(self):
        """A 500ms timeout should kill within ~600ms wall clock."""
        t0 = time.perf_counter()
        with make_sandbox() as sb:
            result = sb.execute("while True: pass", timeout_ms=500)
        elapsed = (time.perf_counter() - t0) * 1000
        assert result.failed
        assert elapsed < 1500, f"Kill took too long: {elapsed:.0f}ms"


# ─────────────────────────────────────────────────────────────────────────────
# Tier resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestTierResolution:
    def test_auto_resolves_to_t3_when_no_t1_t2(self):
        """Without Cranelift FFI or wasmtime, auto must pick t3_subprocess."""
        sb = make_sandbox(preferred_tier="auto")
        assert sb._resolve_tier() == "t3_subprocess"

    def test_explicit_t3_always_uses_t3(self):
        sb = make_sandbox(preferred_tier="t3_subprocess")
        assert sb._resolve_tier() == "t3_subprocess"

    def test_execution_tier_in_result(self):
        with make_sandbox(preferred_tier="t3_subprocess") as sb:
            result = sb.execute("pass")
        assert result.tier == "t3_subprocess"


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot stubs (Phase 3 readiness)
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotStubs:
    def test_snapshot_returns_handle(self):
        sb = make_sandbox()
        handle = sb.snapshot()
        assert handle.snapshot_id
        assert handle.project_root

    def test_restore_does_not_raise(self):
        sb = make_sandbox()
        handle = sb.snapshot()
        sb.restore(handle)  # Phase 3 stub — must not raise


# ─────────────────────────────────────────────────────────────────────────────
# PatchEngine end-to-end: run_script through real sandbox
# ─────────────────────────────────────────────────────────────────────────────

class TestRunScriptEndToEnd:
    def _make_run_script_patch(self, payload: str) -> dict:
        return {
            "schema_version": "1.0",
            "patch_id": str(uuid.uuid4()),
            "action": "run_script",
            "target": {"file": "scripts/run.py"},
            "changes": {"operation": "run", "payload": payload},
        }

    def test_run_script_valid_payload_executes(self):
        from ai_runtime import PatchEngine
        engine = PatchEngine()
        patch  = self._make_run_script_patch("x = 1 + 1\nprint(x)")
        report = engine.validate(patch, trust_level="elevated")
        assert report.ok, report.first_error
        result = engine.apply(patch)
        assert result is not None
        assert result.succeeded
        assert "2" in result.stdout

    def test_run_script_failing_payload_returns_failed_result(self):
        from ai_runtime import PatchEngine
        engine = PatchEngine()
        patch  = self._make_run_script_patch("raise RuntimeError('test')")
        report = engine.validate(patch, trust_level="elevated")
        assert report.ok
        result = engine.apply(patch)
        assert result.failed

    def test_run_script_without_trust_rejected_before_sandbox(self):
        """run_script without elevation must be rejected at Gate 2 — sandbox never called."""
        from ai_runtime import PatchEngine
        engine = PatchEngine()
        patch  = self._make_run_script_patch("print('should not run')")
        report = engine.validate(patch)   # standard trust
        assert not report.ok
        assert any("elevated" in e or "trust" in e.lower() for e in report.errors)

    def test_run_script_with_sandbox_instance(self):
        """PatchEngine accepts an explicit Sandbox instance."""
        from ai_runtime import PatchEngine
        sb     = Sandbox(project_root=".")
        engine = PatchEngine(sandbox=sb)
        patch  = self._make_run_script_patch("print('explicit sandbox')")
        report = engine.validate(patch, trust_level="elevated")
        assert report.ok
        result = engine.apply(patch)
        assert "explicit sandbox" in result.stdout

    def test_sandbox_performance_slo(self):
        """T3 execution overhead SLO: < 500ms (excluding payload runtime)."""
        with make_sandbox() as sb:
            result = sb.execute("pass", timeout_ms=5000)
        assert result.succeeded
        assert result.elapsed_ms < 500, (
            f"T3 overhead was {result.elapsed_ms:.1f}ms, SLO is <500ms"
        )
