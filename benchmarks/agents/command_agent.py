#!/usr/bin/env python
"""Command-backed benchmark agent adapter.

The external command receives the task descriptor JSON on stdin and should
print either a patch object or an envelope with a `patch` object and optional
usage metadata:

{
  "patch": { "...": "Aether patch JSON" },
  "usage": {
    "input_tokens": 123,
    "output_tokens": 45,
    "tool_calls": 2,
    "latency_ms": 900,
    "cost_usd": 0.0012
  }
}
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command-backed benchmark agent.")
    parser.add_argument("task_descriptor", help="Path to a JSON task descriptor.")
    parser.add_argument("--retries", type=int, default=0, help="Retry count after the first attempt.")
    parser.add_argument("--timeout-ms", type=int, default=120000, help="Per-attempt timeout.")
    parser.add_argument("--command", nargs=argparse.REMAINDER, required=True, help="Command to run.")
    args = parser.parse_args()

    if not args.command:
        print("--command requires at least one executable argument.", file=sys.stderr)
        return 2

    descriptor = Path(args.task_descriptor).read_text(encoding="utf-8")
    attempts: list[dict[str, Any]] = []
    started = time.perf_counter()

    for attempt in range(1, args.retries + 2):
        attempt_started = time.perf_counter()
        try:
            result = subprocess.run(
                args.command,
                input=descriptor,
                text=True,
                capture_output=True,
                timeout=args.timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            attempts.append({
                "attempt": attempt,
                "ok": False,
                "elapsed_ms": elapsed_ms(attempt_started),
                "error": f"timeout after {args.timeout_ms}ms",
                "stdout": exc.stdout,
                "stderr": exc.stderr,
            })
            continue

        attempts.append({
            "attempt": attempt,
            "ok": result.returncode == 0,
            "elapsed_ms": elapsed_ms(attempt_started),
            "returncode": result.returncode,
            "stderr": result.stderr,
        })
        if result.returncode != 0:
            continue

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            attempts[-1]["ok"] = False
            attempts[-1]["error"] = f"invalid JSON: {exc}"
            continue

        envelope = normalize_payload(payload)
        envelope.setdefault("agent", {})
        envelope["agent"].update({
            "adapter": "command_agent",
            "attempts": attempts,
            "attempt_count": attempt,
            "elapsed_ms": elapsed_ms(started),
        })
        print(json.dumps(envelope, sort_keys=True))
        return 0

    print(json.dumps({
        "agent": {
            "adapter": "command_agent",
            "attempts": attempts,
            "attempt_count": len(attempts),
            "elapsed_ms": elapsed_ms(started),
        },
        "error": "command agent failed all attempts",
    }, sort_keys=True), file=sys.stderr)
    return 1


def normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("patch"), dict):
        return payload
    if isinstance(payload, dict) and {"schema_version", "patch_id", "action", "target", "changes"} <= set(payload):
        return {"patch": payload, "usage": {}}
    raise ValueError("Agent output must be a patch object or an envelope containing patch.")


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


if __name__ == "__main__":
    raise SystemExit(main())
