"""
ai_runtime.sandbox_runner
~~~~~~~~~~~~~~~~~~~~~~~~~~
Worker script executed INSIDE the subprocess sandbox.

This file is spawned as a child process by T3SubprocessSandbox.
It receives the payload via stdin (JSON), executes it with exec(),
captures stdout/stderr, and writes a result JSON to a result file.

Security notes:
  - This runs in an already-isolated process (OS-level isolation enforced
    by the parent before spawn). Do not add further Python-level checks here;
    the OS resource limits and process isolation are the actual security boundary.
  - stdout/stderr are captured and returned to the parent; the child cannot
    write to the parent's filesystem beyond its working directory.
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback


def main() -> None:
    # Read the payload JSON from stdin
    try:
        request = json.loads(sys.stdin.read())
        payload = request["payload"]
        result_path = request["result_path"]
    except Exception as e:
        # Can't even parse the request — exit with code 2
        sys.exit(2)

    # Redirect stdout/stderr so we can capture them
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = captured_out
    sys.stderr = captured_err

    exit_code = 0
    error_msg = None

    try:
        # Compile first to get a useful SyntaxError message if invalid
        code = compile(payload, "<ai_patch>", "exec")
        exec(code, {"__name__": "__main__"})  # noqa: S102
    except SystemExit as e:
        exit_code = int(e.code) if e.code is not None else 0
    except Exception:
        exit_code = 1
        error_msg = traceback.format_exc()
        sys.stderr.write(error_msg)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    result = {
        "exit_code": exit_code,
        "stdout":    captured_out.getvalue(),
        "stderr":    captured_err.getvalue(),
        "error":     error_msg,
    }

    # Write result to the agreed temp file path
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except Exception:
        pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
