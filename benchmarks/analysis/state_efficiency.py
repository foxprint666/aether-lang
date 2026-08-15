#!/usr/bin/env python
"""Compare control, state-transition fast path, and full Aether modes."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()
    records: list[dict[str, Any]] = []
    experiments: list[str] = []
    for path in args.raw_results:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
        if payload.get("experiment_id"):
            experiments.append(str(payload["experiment_id"]))
    triples = matched_triples(records)
    pairs = matched_pairs(records)
    report = {
        "experiments": experiments,
        "record_count": len(records),
        "triples": summarize_triples(triples),
        "pairs": {
            "control_vs_state": compare_pair_list(pairs.get(("control", "state"), [])),
            "control_vs_aether": compare_pair_list(pairs.get(("control", "aether"), [])),
            "state_vs_aether": compare_pair_list(pairs.get(("state", "aether"), [])),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare benchmark execution modes.")
    parser.add_argument("raw_results", nargs="+", type=Path, help="Raw benchmark result JSON files.")
    return parser.parse_args()


def matched_triples(records: list[dict[str, Any]]) -> list[dict[str, dict[str, Any]]]:
    by_key: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for record in records:
        if not record.get("expected_success", record.get("configuration", {}).get("expected_success", True)):
            continue
        config = record.get("configuration") if isinstance(record.get("configuration"), dict) else {}
        mode = config.get("mode")
        if mode in {"control", "state", "aether"} and patch_was_tested(record):
            by_key.setdefault((record.get("task_id"), config.get("trial")), {})[mode] = record
    return [item for item in by_key.values() if {"control", "state", "aether"} <= set(item)]


def matched_pairs(records: list[dict[str, Any]]) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    by_key: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for record in records:
        config = record.get("configuration") if isinstance(record.get("configuration"), dict) else {}
        if config.get("expected_success") is not True:
            continue
        mode = config.get("mode")
        if mode in {"control", "state", "aether"} and patch_was_tested(record):
            by_key.setdefault((record.get("task_id"), config.get("trial")), {})[mode] = record

    out: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for left, right in [("control", "state"), ("control", "aether"), ("state", "aether")]:
        out[(left, right)] = [
            (item[left], item[right])
            for item in by_key.values()
            if {left, right} <= set(item)
        ]
    return out


def summarize_triples(triples: list[dict[str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "n": len(triples),
        "success": {
            mode: rate(sum(1 for item in triples if item[mode].get("task_success")), len(triples))
            for mode in ["control", "state", "aether"]
        },
        "execution_time_ms": {
            mode: mean([item[mode].get("execution_time_ms") for item in triples])
            for mode in ["control", "state", "aether"]
        },
    }


def compare_pair_list(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    left_times = [left.get("execution_time_ms") for left, _ in pairs]
    right_times = [right.get("execution_time_ms") for _, right in pairs]
    left_mean = mean(left_times)
    right_mean = mean(right_times)
    return {
        "n": len(pairs),
        "left_success_rate": rate(sum(1 for left, _ in pairs if left.get("task_success")), len(pairs)),
        "right_success_rate": rate(sum(1 for _, right in pairs if right.get("task_success")), len(pairs)),
        "left_execution_mean_ms": left_mean,
        "right_execution_mean_ms": right_mean,
        "right_overhead_pct": pct_delta(right_mean, left_mean),
        "right_savings_pct": pct_savings(left_mean, right_mean),
        "right_efficiency_pct": pct_efficiency(left_mean, right_mean),
    }


def patch_was_tested(record: dict[str, Any]) -> bool:
    return (
        record.get("patch_size") is not None
        or record.get("validation_failed") is True
        or record.get("rollback_triggered") is True
        or record.get("execution_time_ms") is not None
    )


def mean(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    if not numbers:
        return None
    return round(statistics.fmean(numbers), 6)


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def pct_delta(right: float | None, left: float | None) -> float | None:
    if right is None or left is None or left <= 0:
        return None
    return round((right - left) / left * 100, 6)


def pct_savings(left: float | None, right: float | None) -> float | None:
    if right is None or left is None or left <= 0:
        return None
    return round((left - right) / left * 100, 6)


def pct_efficiency(left: float | None, right: float | None) -> float | None:
    if right is None or left is None or right <= 0:
        return None
    return round(left / right * 100, 6)


if __name__ == "__main__":
    raise SystemExit(main())
