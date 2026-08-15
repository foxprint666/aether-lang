#!/usr/bin/env python
"""Summarize raw Aether benchmark output.

Usage:
    python benchmarks/analysis/summarize.py benchmarks/results/raw/<run>.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


NUMERIC_FIELDS = [
    "patch_size",
    "input_tokens",
    "output_tokens",
    "tool_calls",
    "agent_attempts",
    "agent_latency_ms",
    "agent_cost_usd",
    "execution_time_ms",
    "validation_time_ms",
    "tests_passed",
    "tests_failed",
]


def main() -> int:
    args = parse_args()
    payload = json.loads(args.raw_result.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    summary = {
        "experiment_id": payload.get("experiment_id"),
        "benchmark_version": payload.get("benchmark_version"),
        "record_count": len(records),
        "overall": summarize_records(records),
        "by_mode": group_summary(records, lambda r: r["configuration"]["mode"]),
        "by_language": group_summary(records, lambda r: r["language"]),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize raw benchmark JSON.")
    parser.add_argument("raw_result", type=Path, help="Path to benchmarks/results/raw/*.json")
    return parser.parse_args()


def group_summary(records: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(key_fn(record)), []).append(record)
    return {key: summarize_records(items) for key, items in sorted(grouped.items())}


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    successes = sum(1 for record in records if record.get("task_success"))
    return {
        "records": total,
        "task_successes": successes,
        "task_failures": total - successes,
        "success_rate": rate(successes, total),
        "fields": {
            field: numeric_summary([record.get(field) for record in records])
            for field in NUMERIC_FIELDS
        },
    }


def numeric_summary(values: list[Any]) -> dict[str, Any]:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "stdev": None,
            "min": None,
            "max": None,
        }
    return {
        "n": len(numeric),
        "mean": round(statistics.fmean(numeric), 6),
        "median": round(statistics.median(numeric), 6),
        "stdev": round(statistics.stdev(numeric), 6) if len(numeric) > 1 else 0.0,
        "min": round(min(numeric), 6),
        "max": round(max(numeric), 6),
    }


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


if __name__ == "__main__":
    raise SystemExit(main())
