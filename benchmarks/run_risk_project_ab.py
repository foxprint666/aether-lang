#!/usr/bin/env python
"""Run the risk-project raw-vs-Aether build benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "risk_scoring" / "risk_project_ab.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    result = subprocess.run(
        [sys.executable, str(BENCH), "--json"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    payload["experiment_id"] = args.experiment_id
    payload["commit_sha"] = git_commit_sha()
    raw = ROOT / "benchmarks" / "results" / "raw" / f"{args.experiment_id}.json"
    processed = ROOT / "benchmarks" / "results" / "processed" / f"{args.experiment_id}.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    processed.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(processed, payload["records"])
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


def write_csv(path: Path, records: list[dict]) -> None:
    fields = [
        "mode",
        "success",
        "accuracy",
        "precision",
        "recall",
        "build_ms",
        "verify_ms",
        "generated_tokens",
        "generated_bytes",
        "project_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def git_commit_sha() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
