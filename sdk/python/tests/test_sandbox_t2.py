import pytest
from pathlib import Path
from ai_runtime.sandbox_t2 import T2WasmSandbox

def test_t2_sandbox_basic_execution(tmp_path):
    sandbox = T2WasmSandbox(working_dir=tmp_path)
    result = sandbox.run("print('hello from wasm')")
    assert result.succeeded is True
    assert "hello from wasm" in result.stdout

def test_t2_sandbox_error(tmp_path):
    sandbox = T2WasmSandbox(working_dir=tmp_path)
    result = sandbox.run("raise ValueError('something went wrong')")
    assert result.succeeded is False
    assert "something went wrong" in result.stderr

def test_t2_sandbox_timeout(tmp_path):
    sandbox = T2WasmSandbox(working_dir=tmp_path)
    # A tight CPU loop that doesn't call host blocking functions
    result = sandbox.run("while True: pass", timeout_ms=500)
    assert result.succeeded is False
    assert result.exit_code == 137
    assert "Timeout: execution exceeded" in result.error

def test_t2_sandbox_exit(tmp_path):
    sandbox = T2WasmSandbox(working_dir=tmp_path)
    result = sandbox.run("import sys; sys.exit(42)")
    assert result.succeeded is False
    assert result.exit_code == 42

def test_t2_sandbox_fs_isolation_blocked(tmp_path):
    sandbox = T2WasmSandbox(working_dir=tmp_path)
    # Attempt to write to a file in the working dir. Should fail due to isolation.
    code = """
import os
try:
    with open('test_fs_block.txt', 'w') as f:
        f.write('blocked')
    print('wrote file')
except Exception as e:
    print(f'failed: {type(e).__name__}')
"""
    result = sandbox.run(code, allow_filesystem=False)
    # Execution itself succeeds (exit code 0), but Python caught an OS exception internally
    assert result.succeeded is True
    assert "failed: OSError" in result.stdout or "failed: PermissionError" in result.stdout or "failed: FileNotFoundError" in result.stdout
    assert "wrote file" not in result.stdout
    assert not (tmp_path / "test_fs_block.txt").exists()

def test_t2_sandbox_fs_isolation_allowed(tmp_path):
    sandbox = T2WasmSandbox(working_dir=tmp_path)
    code = """
import os
try:
    with open('test_fs_allow.txt', 'w') as f:
        f.write('allowed')
    print('wrote file')
except Exception as e:
    print(f'failed: {e}')
"""
    result = sandbox.run(code, allow_filesystem=True)
    assert result.succeeded is True
    assert "wrote file" in result.stdout
    assert (tmp_path / "test_fs_allow.txt").exists()
    assert (tmp_path / "test_fs_allow.txt").read_text() == 'allowed'
