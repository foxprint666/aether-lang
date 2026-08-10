"""
tests/test_ffi_fuzz.py
~~~~~~~~~~~~~~~~~~~~~~~
Phase 8 FFI Guard Ring fuzzing suite.

Validates that the Rust `ae_sandbox_execute` / `ae_sandbox_free` ABI guard ring
is robust against:
  - Null-byte injection in payloads
  - Malformed / binary garbage inputs
  - Extremely large payloads
  - Unicode edge-cases (RTL marks, surrogates, emoji)
  - Valid Aether programs (positive smoke tests)

Prerequisites
-------------
The compiled Rust shared library must be reachable by T1CraneliftSandbox.
Run `cargo build --release -p ae-codegen` before executing this suite.

If the library is not found, all tests are skipped gracefully with an
informative message rather than failing.

Run with::
    pytest sdk/python/tests/test_ffi_fuzz.py -v
"""

from __future__ import annotations

import os
import random
import string
import sys
import json
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Fixture — skip whole module if the shared lib is absent
# ─────────────────────────────────────────────────────────────────────────────

def _lib_available() -> bool:
    lib_name = "ae_codegen.dll" if sys.platform == "win32" else (
        "libae_codegen.dylib" if sys.platform == "darwin" else "libae_codegen.so"
    )
    env_path = os.environ.get("AE_CODEGEN_LIB")
    if env_path and Path(env_path).exists():
        return True

    # sdk/python/
    pkg_root = Path(__file__).parent.parent
    if (pkg_root / lib_name).exists():
        return True

    # workspace target/release/
    workspace = pkg_root.parent.parent
    if (workspace / "target" / "release" / lib_name).exists():
        return True

    return False


LIB_AVAILABLE = _lib_available()

pytestmark = pytest.mark.skipif(
    not LIB_AVAILABLE,
    reason=(
        "ae_codegen shared library not found. "
        "Run `cargo build --release -p ae-codegen` to enable T1 tests."
    ),
)


@pytest.fixture(scope="module")
def sandbox():
    """Return a single T1CraneliftSandbox for the whole module."""
    from ai_runtime.sandbox_t1 import T1CraneliftSandbox
    return T1CraneliftSandbox()


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _result_is_valid_json(result) -> bool:
    """Ensure the result object has all mandatory fields."""
    assert hasattr(result, "failed"), "ExecutionResult missing .failed"
    assert hasattr(result, "stdout"), "ExecutionResult missing .stdout"
    assert hasattr(result, "stderr"), "ExecutionResult missing .stderr"
    assert hasattr(result, "elapsed_ms"), "ExecutionResult missing .elapsed_ms"
    assert result.tier == "t1_cranelift", f"Unexpected tier: {result.tier}"
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Positive smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestValidPrograms:
    """Valid Aether programs should return success=True."""

    def test_empty_program(self, sandbox):
        result = sandbox.run("")
        # Empty source may succeed or produce a parse error — but must not crash
        assert _result_is_valid_json(result)

    def test_integer_arithmetic(self, sandbox):
        result = sandbox.run("let x = 1 + 2;")
        assert _result_is_valid_json(result)
        assert not result.failed, f"Expected success, got: {result.stderr}"

    def test_let_binding(self, sandbox):
        result = sandbox.run("let answer = 6 * 7;")
        assert not result.failed, result.stderr

    def test_if_else(self, sandbox):
        code = "let x = 10;\nif x > 5 { let y = 1; } else { let y = 0; }"
        result = sandbox.run(code)
        assert not result.failed, result.stderr

    def test_while_loop(self, sandbox):
        code = "let i = 0;\nwhile i < 3 { let i = i + 1; }"
        result = sandbox.run(code)
        # Variable mutation semantics may vary; must not crash
        assert _result_is_valid_json(result)

    def test_function_definition(self, sandbox):
        code = "fn add(a: int, b: int) -> int { return a + b; }\nlet r = add(3, 4);"
        result = sandbox.run(code)
        # The interpreter may or may not support typed params yet; must not crash
        assert _result_is_valid_json(result)

    def test_nested_function_calls(self, sandbox):
        code = """
fn square(x: int) -> int { return x * x; }
fn cube(x: int) -> int { return x * square(x); }
let r = cube(3);
"""
        result = sandbox.run(code)
        assert _result_is_valid_json(result)


# ─────────────────────────────────────────────────────────────────────────────
# Parse-error inputs
# ─────────────────────────────────────────────────────────────────────────────

class TestParseErrors:
    """Inputs that should be rejected by the parser — must not crash the process."""

    @pytest.mark.parametrize("bad_src", [
        "fn (((broken",
        "let = ;",
        "}}}}}}",
        "if { } else",
        "while { }",
        "return return return",
        "fn fn fn fn",
        "let x = (",
        "}}}let x = 1{{{",
    ])
    def test_parse_error_returns_failure(self, sandbox, bad_src):
        result = sandbox.run(bad_src)
        assert _result_is_valid_json(result)
        assert result.failed, f"Expected failure for input: {bad_src!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Null-byte injection
# ─────────────────────────────────────────────────────────────────────────────

class TestNullByteInjection:
    """Null bytes inside a Python string are stripped at encode time; the library
    must handle any resulting malformed UTF-8 without panicking."""

    def test_null_in_middle(self, sandbox):
        # Python will encode \x00 as a literal null byte — CStr::from_ptr will
        # treat it as end-of-string, producing a truncated but valid C-string.
        payload = "let x = 1;\x00DROP TABLE users;"
        result = sandbox.run(payload)
        assert _result_is_valid_json(result)

    def test_null_at_start(self, sandbox):
        result = sandbox.run("\x00let x = 1;")
        assert _result_is_valid_json(result)

    def test_only_null(self, sandbox):
        result = sandbox.run("\x00")
        assert _result_is_valid_json(result)

    def test_many_nulls(self, sandbox):
        result = sandbox.run("\x00" * 100)
        assert _result_is_valid_json(result)


# ─────────────────────────────────────────────────────────────────────────────
# Binary garbage / random bytes
# ─────────────────────────────────────────────────────────────────────────────

class TestBinaryGarbage:
    """Feed the FFI with random non-UTF-8 byte sequences."""

    def _make_garbage(self, seed: int, size: int = 128) -> str:
        rng = random.Random(seed)
        # Mix printable ASCII with high bytes — some will be invalid UTF-8
        chars = []
        for _ in range(size):
            if rng.random() < 0.5:
                chars.append(chr(rng.randint(32, 126)))
            else:
                # High bytes that may form invalid UTF-8
                chars.append(chr(rng.randint(128, 255)))
        return "".join(chars)

    @pytest.mark.parametrize("seed", range(20))
    def test_random_garbage_does_not_crash(self, sandbox, seed):
        payload = self._make_garbage(seed)
        # Must return a valid ExecutionResult, not raise an exception
        result = sandbox.run(payload)
        assert _result_is_valid_json(result)

    def test_all_high_bytes(self, sandbox):
        payload = "".join(chr(i) for i in range(128, 256))
        result = sandbox.run(payload)
        assert _result_is_valid_json(result)

    def test_binary_that_looks_like_json(self, sandbox):
        payload = '{"action": "drop_table", "table": "users\x00"}'
        result = sandbox.run(payload)
        assert _result_is_valid_json(result)


# ─────────────────────────────────────────────────────────────────────────────
# Large payloads
# ─────────────────────────────────────────────────────────────────────────────

class TestLargePayloads:
    """Stress-test the FFI with very large source strings."""

    def test_1_mb_payload(self, sandbox):
        # 1 MB of valid-looking but broken Aether source
        payload = ("let x = 1;\n" * 90_000)[:1_048_576]
        result = sandbox.run(payload)
        assert _result_is_valid_json(result)

    def test_deeply_nested_parens(self, sandbox):
        payload = "let x = " + "(" * 500 + "1" + ")" * 500 + ";"
        result = sandbox.run(payload)
        assert _result_is_valid_json(result)

    def test_long_identifier(self, sandbox):
        long_name = "a" * 10_000
        result = sandbox.run(f"let {long_name} = 1;")
        assert _result_is_valid_json(result)

    def test_many_let_bindings(self, sandbox):
        lines = "\n".join(f"let var_{i} = {i};" for i in range(5000))
        result = sandbox.run(lines)
        assert _result_is_valid_json(result)


# ─────────────────────────────────────────────────────────────────────────────
# Unicode edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestUnicodeEdgeCases:
    """Ensure Unicode-heavy source strings don't crash the parser."""

    def test_emoji_in_string_literal(self, sandbox):
        result = sandbox.run('let s = "hello 🌍";')
        assert _result_is_valid_json(result)

    def test_rtl_marks(self, sandbox):
        # Right-to-left override characters
        result = sandbox.run("let x\u202e = 1;")
        assert _result_is_valid_json(result)

    def test_zero_width_joiners(self, sandbox):
        result = sandbox.run("let x\u200d = 1;")
        assert _result_is_valid_json(result)

    def test_cjk_identifiers(self, sandbox):
        result = sandbox.run("let 変数 = 42;")
        assert _result_is_valid_json(result)

    def test_mixed_unicode_and_nulls(self, sandbox):
        result = sandbox.run("let x = '日本語\x00';\n")
        assert _result_is_valid_json(result)


# ─────────────────────────────────────────────────────────────────────────────
# Repeated calls (regression: no use-after-free or double-free)
# ─────────────────────────────────────────────────────────────────────────────

class TestRepeatedCalls:
    """Call the FFI hundreds of times to catch memory management bugs."""

    def test_100_sequential_calls(self, sandbox):
        for i in range(100):
            result = sandbox.run(f"let x_{i} = {i};")
            assert _result_is_valid_json(result), f"Failed on iteration {i}"

    def test_alternating_valid_invalid(self, sandbox):
        for i in range(50):
            if i % 2 == 0:
                r = sandbox.run(f"let x = {i};")
            else:
                r = sandbox.run("fn (((broken")
            assert _result_is_valid_json(r)

    def test_empty_string_repeated(self, sandbox):
        for _ in range(20):
            result = sandbox.run("")
            assert _result_is_valid_json(result)
