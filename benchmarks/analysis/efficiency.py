#!/usr/bin/env python
"""Calculate matched control-vs-Aether benchmark efficiency.

Usage:
    python benchmarks/analysis/efficiency.py benchmarks/results/raw/<run>.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


FIELDS = [
    "agent_latency_ms",
    "execution_time_ms",
    "validation_time_ms",
    "input_tokens",
    "output_tokens",
    "agent_cost_usd",
]


def main() -> int:
    args = parse_args()
    payload = json.loads(args.raw_result.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if args.legacy_control_agent_timing:
        records = [correct_legacy_control_agent_timing(record) for record in records]
    summary = {
        "experiment_id": payload.get("experiment_id"),
        "legacy_control_agent_timing_corrected": args.legacy_control_agent_timing,
        "matched_pairs": matched_pair_summary(records),
        "aether_safety": safety_summary(records),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate matched benchmark efficiency.")
    parser.add_argument("raw_result", type=Path, help="Path to benchmarks/results/raw/*.json")
    parser.add_argument(
        "--legacy-control-agent-timing",
        action="store_true",
        help="Correct old command-agent control records where execution_time_ms included provider generation.",
    )
    return parser.parse_args()


def correct_legacy_control_agent_timing(record: dict[str, Any]) -> dict[str, Any]:
    config = record.get("configuration") if isinstance(record.get("configuration"), dict) else {}
    if (
        config.get("mode") == "control"
        and record.get("agent") == "command-agent"
        and config.get("agent_generation_excluded_from_execution_time") is not True
        and isinstance(record.get("execution_time_ms"), (int, float))
        and isinstance(record.get("agent_latency_ms"), (int, float))
    ):
        corrected = dict(record)
        corrected["execution_time_ms"] = max(0.0, float(record["execution_time_ms"]) - float(record["agent_latency_ms"]))
        return corrected
    return record


def matched_pair_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for record in records:
        if record.get("category") in {"invalid_patch", "rollback", "failure_injection"}:
            continue
        if record.get("expected_success") is False:
            continue
        config = record.get("configuration") if isinstance(record.get("configuration"), dict) else {}
        key = (record.get("task_id"), config.get("trial"))
        mode = config.get("mode")
        if mode in {"control", "aether"}:
            by_key.setdefault(key, {})[mode] = record

    pairs = [pair for pair in by_key.values() if {"control", "aether"} <= set(pair)]
    evaluable_pairs = [
        pair for pair in pairs
        if patch_was_tested(pair["control"]) and patch_was_tested(pair["aether"])
    ]
    field_summaries = {
        field: compare_field(
            [pair["control"] for pair in evaluable_pairs],
            [pair["aether"] for pair in evaluable_pairs],
            field,
        )
        for field in FIELDS
    }
    return {
        "pairs": len(pairs),
        "evaluable_pairs": len(evaluable_pairs),
        "provider_availability_rate": rate(len(evaluable_pairs), len(pairs)),
        "success": {
            "control_success_rate": rate(
                sum(1 for pair in evaluable_pairs if pair["control"].get("task_success")),
                len(evaluable_pairs),
            ),
            "aether_success_rate": rate(
                sum(1 for pair in evaluable_pairs if pair["aether"].get("task_success")),
                len(evaluable_pairs),
            ),
        },
        "fields": field_summaries,
    }


def compare_field(control_records: list[dict[str, Any]], aether_records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    controls: list[float] = []
    aethers: list[float] = []
    for control, aether in zip(control_records, aether_records):
        control_value = control.get(field)
        aether_value = aether.get(field)
        if isinstance(control_value, (int, float)) and isinstance(aether_value, (int, float)):
            controls.append(float(control_value))
            aethers.append(float(aether_value))

    if not controls:
        return {
            "n": 0,
            "control_mean": None,
            "aether_mean": None,
            "delta": None,
            "overhead_pct": None,
            "savings_pct": None,
            "aether_efficiency_pct": None,
        }

    control_mean = statistics.fmean(controls)
    aether_mean = statistics.fmean(aethers)
    delta = aether_mean - control_mean
    overhead = (delta / control_mean * 100) if control_mean else None
    savings = ((control_mean - aether_mean) / control_mean * 100) if control_mean else None
    efficiency = (control_mean / aether_mean * 100) if aether_mean else None
    return {
        "n": len(controls),
        "control_mean": round(control_mean, 6),
        "aether_mean": round(aether_mean, 6),
        "delta": round(delta, 6),
        "overhead_pct": round(overhead, 6) if overhead is not None else None,
        "savings_pct": round(savings, 6) if savings is not None else None,
        "aether_efficiency_pct": round(efficiency, 6) if efficiency is not None else None,
    }


def safety_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    invalid = [
        record for record in records
        if record.get("category") == "invalid_patch"
        and record.get("configuration", {}).get("mode") == "aether"
    ]
    tested = [record for record in invalid if patch_was_tested(record)]
    detected = [record for record in tested if record.get("failure_detected") or record.get("validation_failed")]
    false_accepts = [
        record for record in tested
        if not record.get("failure_detected") and not record.get("validation_failed")
    ]
    return {
        "invalid_records": len(invalid),
        "tested_invalid_records": len(tested),
        "detection_rate": rate(len(detected), len(tested)),
        "false_acceptance_rate": rate(len(false_accepts), len(tested)),
    }


def patch_was_tested(record: dict[str, Any]) -> bool:
    return (
        record.get("patch_size") is not None
        or record.get("validation_failed") is True
        or record.get("rollback_triggered") is True
        or record.get("execution_time_ms") is not None
    )


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


if __name__ == "__main__":
    raise SystemExit(main())
