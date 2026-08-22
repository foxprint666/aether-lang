#!/usr/bin/env python
"""Publish command-backed unseen agent A/B evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    report = analyze(payload)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(rendered, end="")
    return 0


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload["records"]
    summary = payload["summary"]
    return {
        "report_version": "unseen-agent-ab-evidence-v1",
        "experiment_id": payload["experiment_id"],
        "commit_sha": payload.get("commit_sha"),
        "manifest": payload.get("manifest"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "task_count": payload.get("task_count"),
        "trials": payload.get("trials"),
        "baseline_original_passes": payload.get("baseline_original_passes", []),
        "by_arm": summary["by_arm"],
        "efficiency": {
            "aether_output_token_savings_pct": summary["aether_output_token_savings_pct"],
            "aether_output_byte_savings_pct": summary["aether_output_byte_savings_pct"],
            "aether_success_delta_percentage_points": summary["aether_success_delta_percentage_points"],
        },
        "records": [
            {
                "task_id": item["task_id"],
                "trial": item["trial"],
                "arm": item["arm"],
                "task_success": item["task_success"],
                "output_tokens": item["output_tokens"],
                "total_task_time_ms": item["total_task_time_ms"],
                "error_type": item["error_type"],
            }
            for item in records
        ],
        "limitations": payload.get("limitations", []),
    }


def markdown(report: dict[str, Any]) -> str:
    raw = report["by_arm"]["raw_full_file"]
    aether = report["by_arm"]["aether_patch"]
    efficiency = report["efficiency"]
    lines = [
        "# Unseen Agent A/B Report",
        "",
        f"- Tasks: `{report['task_count']}`; trials: `{report['trials']}`.",
        f"- Raw full-file success: `{raw['successes']}/{raw['records']}` (`{raw['success_rate']}`).",
        f"- Aether patch success: `{aether['successes']}/{aether['records']}` (`{aether['success_rate']}`).",
        f"- Success delta: `{efficiency['aether_success_delta_percentage_points']}` percentage points.",
        f"- Aether output-token savings: `{efficiency['aether_output_token_savings_pct']}%`.",
        f"- Aether output-byte savings: `{efficiency['aether_output_byte_savings_pct']}%`.",
        f"- Raw mean total task time: `{raw['mean_total_task_time_ms']} ms`.",
        f"- Aether mean total task time: `{aether['mean_total_task_time_ms']} ms`.",
        f"- Original revisions already passing hidden checks: `{len(report['baseline_original_passes'])}`.",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
