#!/usr/bin/env python
"""Canonical source-only protocol for paired patch-vs-full-file trials."""

from __future__ import annotations

import hashlib
import json
from typing import Any


PROTOCOL_VERSION = "paired-blind-v1"
PATCH_ARM = "aether_patch"
FULL_FILE_ARM = "full_file"
ARMS = (PATCH_ARM, FULL_FILE_ARM)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_prompt_core(tasks: list[dict[str, Any]], sources: dict[str, str]) -> dict[str, Any]:
    """Return the allowlisted content shared by both generation arms."""
    allowed_tasks = [
        {
            "task": item["task_id"],
            "language": item["language"],
            "source_id": item["source_id"],
            "source_file": item["source_file"],
            "description": item["description"],
        }
        for item in tasks
    ]
    return {
        "protocol_version": PROTOCOL_VERSION,
        "source_only": True,
        "withheld": ["tests", "expected_outputs", "reference_solutions"],
        "sources": sources,
        "tasks": allowed_tasks,
    }


def arm_descriptor(core_hash: str, arm: str, trial: int) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError(f"Unsupported paired arm: {arm}")
    if trial < 1:
        raise ValueError("trial must be >= 1")
    contract = (
        "Return one Aether 1.0 JSON patch per task with a fresh UUID4, exact target file "
        "and bare AST symbol, and no semantic normalization."
        if arm == PATCH_ARM
        else "Return the complete updated target source file per task, preserving unrelated source."
    )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "prompt_core_sha256": core_hash,
        "artifact_format": arm,
        "trial": trial,
        "output_contract": contract,
    }
