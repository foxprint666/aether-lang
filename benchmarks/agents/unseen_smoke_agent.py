#!/usr/bin/env python
"""Deterministic command agent for the unseen A/B smoke protocol."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone


def main() -> int:
    descriptor = json.load(sys.stdin)
    arm = descriptor["arm"]
    if arm == "raw_full_file":
        payload = {"content": full_file_for(descriptor)}
    elif arm == "aether_patch":
        payload = {"patch": patch_for(descriptor)}
    else:
        raise ValueError(f"Unsupported arm: {arm}")
    rendered = json.dumps(payload, sort_keys=True)
    payload["usage"] = {
        "input_tokens": estimate_tokens(json.dumps(descriptor, sort_keys=True)),
        "output_tokens": estimate_tokens(rendered),
        "tool_calls": 1,
        "latency_ms": 0,
        "cost_usd": 0,
        "model": "unseen-smoke-agent",
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def full_file_for(descriptor: dict) -> str:
    task = descriptor["task_id"]
    source = descriptor.get("source", "")
    if task == "ux-py-total":
        return source.replace("    return 0\n", "    return add(20, 22)\n")
    if task == "ux-py-block":
        return source.replace("    value = 2\n", "    value = 42\n")
    if task == "ux-js-total":
        return source.replace("  return 0;\n", "  return add(20, 22);\n")
    if task == "ux-js-block":
        return source.replace("  value = 2;\n", "  value = 42;\n")
    raise ValueError(f"Unknown smoke task: {task}")


def patch_for(descriptor: dict) -> dict:
    task = descriptor["task_id"]
    source_file = descriptor["source_file"]
    if task == "ux-py-total":
        return patch("modify_function", source_file, "total", "function", "replace_body", "return add(20, 22)\n")
    if task == "ux-py-block":
        return {
            **base_patch("replace_block", source_file),
            "changes": {
                "operation": "context_replace",
                "context_before": "    value = 1\n",
                "context_after": "    return value\n",
                "payload": "    value = 42",
            },
        }
    if task == "ux-js-total":
        return patch("modify_function", source_file, "total", "function", "replace_body", "return add(20, 22);")
    if task == "ux-js-block":
        return {
            **base_patch("replace_block", source_file),
            "changes": {
                "operation": "context_replace",
                "context_before": "  let value = 1;\n",
                "context_after": "  return value;\n",
                "payload": "  value = 42;",
            },
        }
    raise ValueError(f"Unknown smoke task: {task}")


def patch(action: str, source_file: str, symbol: str, symbol_type: str, operation: str, payload: str) -> dict:
    value = base_patch(action, source_file)
    value["target"].update({"symbol": symbol, "symbol_type": symbol_type})
    value["changes"] = {"operation": operation, "payload": payload}
    return value


def base_patch(action: str, source_file: str) -> dict:
    return {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": action,
        "target": {"file": source_file},
        "changes": {},
        "metadata": {
            "agent_id": "unseen-smoke-agent",
            "model": "unseen-smoke-agent",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        },
    }


def estimate_tokens(text: str) -> int:
    return len([part for part in text.replace("\n", " ").split(" ") if part])


if __name__ == "__main__":
    raise SystemExit(main())
