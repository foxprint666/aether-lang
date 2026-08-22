#!/usr/bin/env python
"""Run the local raw-vs-Aether RAG benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHATBOT = ROOT / "benchmarks" / "rag" / "local_rag_chatbot.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    result = subprocess.run(
        [sys.executable, str(CHATBOT), "--eval", "--json"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    payload.update({
        "experiment_id": args.experiment_id,
        "commit_sha": git_commit_sha(),
    })
    raw = ROOT / "benchmarks" / "results" / "raw" / f"{args.experiment_id}.json"
    processed = ROOT / "benchmarks" / "results" / "processed" / f"{args.experiment_id}.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    processed.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(processed, payload["records"])
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


def write_csv(path: Path, records: list[dict]) -> None:
    rows = []
    for record in records:
        for mode in ("raw", "aether"):
            row = {"id": record["id"], "question": record["question"], "mode": mode}
            row.update({k: v for k, v in record[mode].items() if not isinstance(v, (list, dict))})
            rows.append(row)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def git_commit_sha() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


if __name__ == "__main__":
    raise SystemExit(main())

