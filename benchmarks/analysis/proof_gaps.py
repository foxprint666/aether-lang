#!/usr/bin/env python
"""Explain what prevents benchmark evidence from reaching 100%.

This complements `proof_score.py`: the proof score is intentionally
conservative, while this report separates tested-scope pass rate from the
remaining evidence and efficiency gaps.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()
    proof = run_proof_score(args.raw_results)
    records = load_records(args.raw_results)
    tested = [record for record in records if patch_was_tested(record)]
    passed = [record for record in tested if record.get("task_success")]
    categories = proof.get("category_scores_pct", {})
    blockers = {
        name: round(100.0 - float(score), 3)
        for name, score in categories.items()
        if isinstance(score, (int, float)) and float(score) < 100.0
    }
    report = {
        "tested_scope": {
            "tested_records": len(tested),
            "passed_records": len(passed),
            "pass_rate_pct": pct(len(passed), len(tested)),
        },
        "conservative_proof_score_pct": proof.get("overall_proof_score_pct"),
        "interpretation": proof.get("interpretation"),
        "blockers_to_100_pct": dict(sorted(blockers.items(), key=lambda item: item[1], reverse=True)),
        "metrics": proof.get("metrics", {}),
        "note": (
            "A 100% tested-scope pass rate means every executed record passed. "
            "A 100% conservative proof score additionally requires full marks for "
            "token efficiency, time efficiency, correctness, safety, repeatability, "
            "provider quality, and real-repository coverage."
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report gaps between current evidence and 100%.")
    parser.add_argument("raw_results", nargs="+", type=Path, help="Raw benchmark result JSON files.")
    return parser.parse_args()


def run_proof_score(paths: list[Path]) -> dict[str, Any]:
    script = Path(__file__).with_name("proof_score.py")
    result = subprocess.run(
        [sys.executable, str(script), *(str(path) for path in paths)],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
    return records


def patch_was_tested(record: dict[str, Any]) -> bool:
    return (
        record.get("patch_size") is not None
        or record.get("validation_failed") is True
        or record.get("rollback_triggered") is True
        or record.get("execution_time_ms") is not None
    )


def pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 3)


if __name__ == "__main__":
    raise SystemExit(main())
