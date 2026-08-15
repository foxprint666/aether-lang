#!/usr/bin/env python
"""Local mock provider for testing the command agent adapter.

Reads a task descriptor from stdin and emits a deterministic patch in the same
envelope shape expected from live provider adapters.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone


def main() -> int:
    descriptor = json.load(sys.stdin)
    patch_spec = patch_for_task(descriptor)
    patch = {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": patch_spec["action"],
        "target": dict(patch_spec["target"]),
        "changes": dict(patch_spec["changes"]),
        "metadata": {
            "agent_id": "mock-provider",
            "model": "mock-provider",
            "intent": str(descriptor.get("description", ""))[:500],
            "created_at": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        },
    }
    print(json.dumps({
        "patch": patch,
        "usage": {
            "input_tokens": 100,
            "output_tokens": len(json.dumps(patch)),
            "tool_calls": 1,
            "latency_ms": 0,
            "cost_usd": 0,
            "model": "mock-provider"
        }
    }, sort_keys=True))
    return 0


def patch_for_task(descriptor: dict) -> dict:
    task_id = descriptor["task_id"]
    source_file = descriptor.get("source_file") or (
        "app.js" if descriptor.get("language") == "javascript" else "app.py"
    )
    if task_id == "agent-py-modify-function":
        return {
            "action": "modify_function",
            "target": {"file": source_file, "symbol": "total", "symbol_type": "function"},
            "changes": {"operation": "replace_body", "payload": "return add(20, 22)\n"},
        }
    if task_id == "agent-py-add-import":
        return {
            "action": "update_import",
            "target": {"file": source_file},
            "changes": {"operation": "add_import", "imports": ["import json", "from pathlib import Path"]},
        }
    if task_id == "agent-py-replace-block":
        return {
            "action": "replace_block",
            "target": {"file": source_file},
            "changes": {
                "operation": "context_replace",
                "context_before": "    value = 1\n",
                "context_after": "    return value\n",
                "payload": "    value = 42",
            },
        }
    if task_id == "agent-js-modify-function":
        return {
            "action": "modify_function",
            "target": {"file": source_file, "symbol": "total", "symbol_type": "function"},
            "changes": {"operation": "replace_body", "payload": "return add(20, 22);"},
        }
    if task_id == "agent-js-add-function":
        return {
            "action": "add_function",
            "target": {"file": source_file, "symbol": "total", "symbol_type": "function"},
            "changes": {"operation": "replace_body", "payload": "function total() {\n  return add(20, 22);\n}"},
        }
    if task_id == "agent-js-remove-function":
        return {
            "action": "remove_function",
            "target": {"file": source_file, "symbol": "unused", "symbol_type": "function"},
            "changes": {"operation": "replace_body"},
        }
    if task_id == "agent-js-replace-block":
        return {
            "action": "replace_block",
            "target": {"file": source_file},
            "changes": {
                "operation": "context_replace",
                "context_before": "  let value = 1;\n",
                "context_after": "  return value;\n",
                "payload": "  value = 42;",
            },
        }
    if task_id == "agent-py-invalid-sensitive-path":
        return {
            "action": "update_import",
            "target": {"file": ".env"},
            "changes": {"operation": "add_import", "imports": ["import os"]},
        }
    if task_id == "agent-js-invalid-sensitive-path":
        return {
            "action": "update_import",
            "target": {"file": ".env"},
            "changes": {"operation": "add_import", "imports": ["const fs = require('fs');"]},
        }
    raise ValueError(f"Mock provider has no deterministic patch for task {task_id}")


if __name__ == "__main__":
    raise SystemExit(main())
