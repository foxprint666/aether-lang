#!/usr/bin/env python
"""Shared descriptor format for blind external-agent benchmark trials."""

from __future__ import annotations

import hashlib
import json
from typing import Any


BLIND_PROTOCOL_VERSION = "blind-external-v1"


def build_blind_descriptor(
    *,
    task: str,
    trial: int,
    language: str,
    repository: str,
    fixture: str,
    source_file: str,
    description: str,
    source: str,
) -> dict[str, Any]:
    """Build a blind task descriptor and attach its canonical digest."""
    if not task:
        raise ValueError("task must not be empty")
    if trial < 1:
        raise ValueError("trial must be >= 1")

    descriptor: dict[str, Any] = {
        "protocol_version": BLIND_PROTOCOL_VERSION,
        "task": task,
        "trial": trial,
        "language": language,
        "repository": repository,
        "fixture": fixture,
        "source_file": source_file,
        "description": description,
        "source": source,
        "aether_patch_contract": {
            "schema_version": "1.0",
            "additional_properties": False,
            "required": ["schema_version", "patch_id", "action", "target", "changes"],
            "patch_id": "A UUID string; generate a fresh UUID4.",
            "target": {
                "file": source_file,
                "symbol": "Required for symbol-oriented actions. Use the exact bare AST identifier; for a class method use clear, not Queue.clear.",
                "symbol_type": "One of function, class, method, variable, import, or module.",
                "additional_properties": False,
            },
            "actions": {
                "modify_function": {
                    "operations": ["replace_body", "update_logic", "insert_before", "insert_after"],
                    "changes": "Supply operation and payload.",
                },
                "add_function": {
                    "operations": ["replace_body"],
                    "changes": "Supply operation and payload.",
                },
                "remove_function": {
                    "operations": ["replace_body"],
                    "changes": "Target the function symbol; payload may be empty.",
                },
                "modify_class": {
                    "operations": ["replace_body", "insert_before", "insert_after"],
                    "changes": "Supply operation and payload.",
                },
                "update_import": {
                    "operations": ["add_import", "remove_import"],
                    "changes": "Supply operation and an imports array.",
                },
                "replace_block": {
                    "operations": ["context_replace"],
                    "changes": "Supply exact context_before, exact context_after when useful, and payload from the visible source.",
                },
            },
            "changes_additional_properties": False,
            "metadata": {
                "optional": True,
                "additional_properties": False,
                "allowed_fields": ["agent_id", "model", "intent", "created_at"],
                "created_at_format": "ISO 8601 date-time when supplied.",
                "instruction": "Omit metadata unless every key follows this contract.",
            },
            "output": "Return one JSON object conforming to this Aether action contract.",
        },
        "withheld_notice": (
            "Tests, reference patches, expected outputs, and acceptance criteria are intentionally "
            "withheld. Produce the change from the description and source alone."
        ),
    }
    descriptor["descriptor_sha256"] = canonical_descriptor_sha256(descriptor)
    return descriptor


def canonical_descriptor_sha256(descriptor: dict[str, Any]) -> str:
    """Hash canonical descriptor JSON, excluding the self-referential digest."""
    payload = dict(descriptor)
    payload.pop("descriptor_sha256", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def descriptor_sha256(descriptor: dict[str, Any]) -> str:
    """Compatibility name for the canonical descriptor digest."""
    return canonical_descriptor_sha256(descriptor)
