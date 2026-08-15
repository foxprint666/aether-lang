#!/usr/bin/env python
"""Deterministic benchmark agent that replays a manifest patch.

The replay agent is intentionally not an LLM. It exercises the benchmark
agent-ingestion path with stable, auditable inputs before provider-specific
adapters are added.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a benchmark patch from a task descriptor.")
    parser.add_argument("task_descriptor", help="Path to a JSON task descriptor.")
    args = parser.parse_args()

    descriptor = load_json(Path(args.task_descriptor))
    patch_spec = descriptor.get("patch")
    if not isinstance(patch_spec, dict):
        print("Task descriptor must contain a patch object.", file=sys.stderr)
        return 2

    patch = {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": patch_spec["action"],
        "target": dict(patch_spec["target"]),
        "changes": dict(patch_spec["changes"]),
        "metadata": {
            "agent_id": "replay-agent",
            "model": "replay-agent",
            "intent": str(descriptor.get("description", ""))[:500],
            "created_at": now_iso(),
        },
    }
    print(json.dumps(patch, sort_keys=True))
    return 0


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Task descriptor must be a JSON object.")
    return payload


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
