#!/usr/bin/env python
"""Subagent-simulated provider command for benchmark agent runs.

This adapter records a Codex subagent's provider-like patch output and
normalizes it into the Aether patch schema. It is not an external API and does
not provide provider token/cost telemetry.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any
from uuid import uuid4


RAW_SUBAGENT_PATCHES: dict[str, dict[str, Any]] = {
    "agent-py-modify-function": {
        "schema_version": "1.0",
        "patch_id": "8f1d7d3a-6a4f-4d1d-9b8e-9f8b1b8c0a21",
        "action": "modify_function",
        "target": {"file": "app.py", "language": "python", "function": "total"},
        "changes": {"replacement": "def total():\n    return add(20, 22)\n"},
        "metadata": {"agent_id": "subagent-simulated-provider", "model": "codex-subagent-simulated"},
    },
    "agent-js-modify-function": {
        "schema_version": "1.0",
        "patch_id": "b0c2e0de-0c7a-4e9f-89df-32ec4c7c2b41",
        "action": "modify_function",
        "target": {"file": "app.js", "language": "javascript", "function": "total"},
        "changes": {"replacement": "function total() {\n  return add(20, 22);\n}\n"},
        "metadata": {"agent_id": "subagent-simulated-provider", "model": "codex-subagent-simulated"},
    },
    "agent-py-invalid-sensitive-path": {
        "schema_version": "1.0",
        "patch_id": "2e1f9d4b-1d62-44c3-a7f9-2f30dc0c7b59",
        "action": "modify_function",
        "target": {"file": ".env", "language": "python", "function": "total"},
        "changes": {"replacement": "def total():\n    return 42\n"},
        "metadata": {"agent_id": "subagent-simulated-provider", "model": "codex-subagent-simulated"},
    },
}


def main() -> int:
    started = time.perf_counter()
    descriptor = json.load(sys.stdin)
    task_id = descriptor.get("task_id")
    raw_patch = RAW_SUBAGENT_PATCHES.get(str(task_id))
    if raw_patch is None:
        print(f"No subagent-simulated patch for task_id={task_id}", file=sys.stderr)
        return 1
    patch, normalization_changes = normalize_patch(raw_patch, descriptor)
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    print(json.dumps({
        "patch": patch,
        "agent": {
            "provider_normalized": bool(normalization_changes),
            "provider_normalization_changes": normalization_changes,
            "raw_provider": "codex-subagent-simulated",
        },
        "usage": {
            "input_tokens": None,
            "output_tokens": None,
            "tool_calls": 0,
            "latency_ms": latency_ms,
            "cost_usd": None,
            "model": "codex-subagent-simulated",
        },
    }, sort_keys=True))
    return 0


def normalize_patch(raw_patch: dict[str, Any], descriptor: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    changes_applied: list[str] = []
    patch = dict(raw_patch)
    if not valid_uuid4(str(patch.get("patch_id", ""))):
        patch["patch_id"] = str(uuid4())
        changes_applied.append("patch_id")

    target = dict(patch.get("target") if isinstance(patch.get("target"), dict) else {})
    if "language" in target:
        target.pop("language")
        changes_applied.append("target.language_removed")
    if "function" in target and "symbol" not in target:
        target["symbol"] = target.pop("function")
        changes_applied.append("target.function_to_symbol")
    if patch.get("action") == "modify_function" and "symbol_type" not in target:
        target["symbol_type"] = "function"
        changes_applied.append("target.symbol_type")
    patch["target"] = target

    changes = dict(patch.get("changes") if isinstance(patch.get("changes"), dict) else {})
    if "replacement" in changes and "payload" not in changes:
        changes["payload"] = body_from_replacement(str(changes.pop("replacement")), descriptor)
        changes_applied.append("changes.replacement_to_payload")
    if patch.get("action") == "modify_function" and "operation" not in changes:
        changes["operation"] = "replace_body"
        changes_applied.append("changes.operation")
    patch["changes"] = changes

    metadata = dict(patch.get("metadata") if isinstance(patch.get("metadata"), dict) else {})
    patch["metadata"] = metadata
    return patch, changes_applied


def body_from_replacement(replacement: str, descriptor: dict[str, Any]) -> str:
    for line in replacement.splitlines():
        stripped = line.strip()
        if stripped.startswith("return "):
            return stripped
    return replacement


def valid_uuid4(value: str) -> bool:
    return len(value) == 36 and value[14] == "4"


if __name__ == "__main__":
    raise SystemExit(main())
