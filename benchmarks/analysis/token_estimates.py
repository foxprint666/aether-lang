#!/usr/bin/env python
"""Summarize offline token estimates from raw benchmark results.

These estimates are not provider billing telemetry. They compare the benchmark
patch JSON output against a full target-file rewrite baseline using the
estimator recorded in each result, preferably `tiktoken:cl100k_base` when the
optional `tiktoken` package is installed.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()
    records = load_records(args.raw_results)
    estimated = [
        record for record in records
        if isinstance(record.get("estimated_output_tokens"), int)
        and isinstance(record.get("estimated_traditional_output_tokens"), int)
        and int(record["estimated_traditional_output_tokens"]) > 0
    ]
    report = {
        "record_count": len(records),
        "estimated_records": len(estimated),
        "overall": summarize(estimated),
        "by_mode": group_summary(estimated, lambda record: record.get("configuration", {}).get("mode")),
        "by_language": group_summary(estimated, lambda record: record.get("language")),
        "estimators": sorted({record.get("token_estimator") for record in estimated if record.get("token_estimator")}),
        "note": (
            "estimated_output_tokens counts structured patch JSON. "
            "estimated_traditional_output_tokens counts the full target source file as a rewrite baseline. "
            "Use provider input_tokens/output_tokens for live billing evidence."
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize offline token estimates.")
    parser.add_argument("raw_results", nargs="+", type=Path, help="Raw benchmark result JSON files.")
    return parser.parse_args()


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
    return records


def group_summary(records: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(key_fn(record))].append(record)
    return {key: summarize(items) for key, items in sorted(grouped.items())}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    patch = [int(record["estimated_output_tokens"]) for record in records]
    traditional = [int(record["estimated_traditional_output_tokens"]) for record in records]
    input_tokens = [
        int(record["estimated_input_tokens"])
        for record in records
        if isinstance(record.get("estimated_input_tokens"), int)
    ]
    patch_total = sum(patch)
    traditional_total = sum(traditional)
    return {
        "records": len(records),
        "estimated_input_tokens": numeric(input_tokens),
        "estimated_patch_output_tokens": numeric(patch),
        "estimated_traditional_output_tokens": numeric(traditional),
        "patch_output_total": patch_total,
        "traditional_output_total": traditional_total,
        "patch_vs_traditional_output_savings_pct": pct_savings(traditional_total, patch_total),
        "patch_output_efficiency_pct": pct_efficiency(traditional_total, patch_total),
    }


def numeric(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6),
        "median": round(statistics.median(values), 6),
        "min": min(values),
        "max": max(values),
    }


def pct_savings(left: int, right: int) -> float | None:
    if left <= 0:
        return None
    return round((left - right) / left * 100, 6)


def pct_efficiency(left: int, right: int) -> float | None:
    if right <= 0:
        return None
    return round(left / right * 100, 6)


if __name__ == "__main__":
    raise SystemExit(main())
