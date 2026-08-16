#!/usr/bin/env python
"""Replay hash-locked patches produced by blind external-agent trials."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from .blind_protocol import BLIND_PROTOCOL_VERSION, canonical_descriptor_sha256
except ImportError:
    from blind_protocol import BLIND_PROTOCOL_VERSION, canonical_descriptor_sha256


DATASET = Path(__file__).with_name("blind_external_agent_trials.json")


def main() -> int:
    descriptor = json.load(sys.stdin)
    if not isinstance(descriptor, dict):
        raise ValueError("Blind descriptor must be a JSON object")
    if descriptor.get("protocol_version") != BLIND_PROTOCOL_VERSION:
        raise ValueError(f"Unsupported blind protocol: {descriptor.get('protocol_version')!r}")

    supplied_digest = descriptor.get("descriptor_sha256")
    computed_digest = canonical_descriptor_sha256(descriptor)
    if not isinstance(supplied_digest, str) or supplied_digest != computed_digest:
        raise ValueError("Descriptor digest is missing or does not match its canonical content")

    dataset = load_object(DATASET)
    record = find_record(dataset, str(descriptor.get("task")), descriptor.get("trial"))
    stored_digest = record.get("descriptor_sha256")
    if stored_digest != supplied_digest:
        raise ValueError("Stored descriptor_sha256 does not exactly match the supplied descriptor")
    patch = record.get("patch")
    if not isinstance(patch, dict):
        raise ValueError("Stored blind trial record must contain a patch object")

    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    agent = {
        "adapter": "blind_external_provider",
        "raw_provider": "stored-blind-external-agent",
        "protocol_version": BLIND_PROTOCOL_VERSION,
        "descriptor_sha256": supplied_digest,
        "task": descriptor.get("task"),
        "trial": descriptor.get("trial"),
        "provider_normalized": False,
        "provider_normalization_changes": [],
        "provenance": provenance,
    }
    usage = {
        "input_tokens": None,
        "output_tokens": None,
        "tool_calls": None,
        "latency_ms": None,
        "cost_usd": None,
        "model": provenance.get("model"),
    }
    print(json.dumps({"patch": patch, "agent": agent, "usage": usage}, sort_keys=True))
    return 0


def find_record(dataset: dict[str, Any], task: str, trial: Any) -> dict[str, Any]:
    trial_key = str(trial)
    records = dataset.get("records")
    if isinstance(records, list):
        matches = [
            item
            for item in records
            if isinstance(item, dict)
            and str(item.get("task")) == task
            and str(item.get("trial")) == trial_key
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one stored blind trial for task={task} trial={trial_key}")
        return matches[0]

    trial_records = dataset.get(trial_key)
    if isinstance(trial_records, dict) and isinstance(trial_records.get(task), dict):
        return trial_records[task]
    raise ValueError(f"No stored blind trial for task={task} trial={trial_key}")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
