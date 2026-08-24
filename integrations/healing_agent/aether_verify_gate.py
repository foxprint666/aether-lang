"""Aether check-only verifier for Healing Agent candidates.

The script follows Healing Agent's VERIFY command contract: exit code 0 accepts
the candidate, nonzero rejects it. JSON stdout is only structured detail for
logs; the exit code is the source of truth.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def main() -> int:
    payload = _candidate_payload()
    if payload is None:
        return _reject("HEALING_AGENT_CANDIDATE is missing or invalid")

    source = Path(str(payload.get("source_file", "")))
    if not source.exists():
        return _reject(f"candidate source does not exist: {source}")

    if source.suffix == ".py":
        try:
            compile(source.read_text(encoding="utf-8"), str(source), "exec")
        except SyntaxError as exc:
            return _reject(f"python syntax rejected: {exc}")

    command = os.getenv("AETHER_VERIFY_COMMAND")
    if command:
        result = subprocess.run(
            _split_command(command),
            cwd=source.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return _reject(detail or f"AETHER_VERIFY_COMMAND exited {result.returncode}")

    print(json.dumps({"ok": True, "source_file": str(source)}))
    return 0


def _candidate_payload() -> Dict[str, Any] | None:
    raw = os.getenv("HEALING_AGENT_CANDIDATE")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _split_command(command: str) -> List[str]:
    parts = shlex.split(command, posix=(os.name != "nt"))
    return [_strip_outer_quotes(part) for part in parts]


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _reject(error: str) -> int:
    print(json.dumps({"ok": False, "error": error}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
