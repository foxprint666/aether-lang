#!/usr/bin/env python
"""Summarize hybrid-mode routing decisions."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()
    records = load_records(args.raw_results)
    hybrid = [
        record for record in records
        if record.get("configuration", {}).get("mode") == "hybrid"
    ]
    report = {
        "record_count": len(records),
        "hybrid_records": len(hybrid),
        "success_rate": rate(sum(1 for record in hybrid if record.get("task_success")), len(hybrid)),
        "selected_modes": dict(Counter(record.get("hybrid_selected_mode") for record in hybrid)),
        "reasons": dict(Counter(record.get("hybrid_reason") for record in hybrid)),
        "token_savings_pct": numeric([
            record.get("hybrid_token_savings_pct")
            for record in hybrid
            if isinstance(record.get("hybrid_token_savings_pct"), (int, float))
        ]),
        "by_language": group_summary(hybrid, lambda record: record.get("language")),
        "by_selected_mode": group_summary(hybrid, lambda record: record.get("hybrid_selected_mode")),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize hybrid benchmark mode decisions.")
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
    return {
        key: {
            "records": len(items),
            "success_rate": rate(sum(1 for item in items if item.get("task_success")), len(items)),
            "selected_modes": dict(Counter(item.get("hybrid_selected_mode") for item in items)),
            "token_savings_pct": numeric([
                item.get("hybrid_token_savings_pct")
                for item in items
                if isinstance(item.get("hybrid_token_savings_pct"), (int, float))
            ]),
        }
        for key, items in sorted(grouped.items())
    }


def numeric(values: list[Any]) -> dict[str, Any]:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    if not numbers:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": len(numbers),
        "mean": round(statistics.fmean(numbers), 6),
        "median": round(statistics.median(numbers), 6),
        "min": round(min(numbers), 6),
        "max": round(max(numbers), 6),
    }


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


if __name__ == "__main__":
    raise SystemExit(main())
