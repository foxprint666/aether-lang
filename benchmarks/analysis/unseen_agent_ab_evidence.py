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
    paired = paired_outcomes(records)
    efficiency = {
        "aether_output_token_savings_pct": summary["aether_output_token_savings_pct"],
        "aether_output_byte_savings_pct": summary["aether_output_byte_savings_pct"],
        "aether_success_delta_percentage_points": summary["aether_success_delta_percentage_points"],
    }
    return {
        "report_version": "unseen-agent-ab-evidence-v2",
        "experiment_id": payload["experiment_id"],
        "commit_sha": payload.get("commit_sha"),
        "manifest": payload.get("manifest"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "task_count": payload.get("task_count"),
        "trials": payload.get("trials"),
        "baseline_original_passes": payload.get("baseline_original_passes", []),
        "by_arm": summary["by_arm"],
        "paired_success": paired,
        "efficiency": efficiency,
        "quality_efficiency_gate": quality_efficiency_gate(efficiency, paired),
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


def paired_outcomes(records: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for item in records:
        pairs.setdefault(item["pair_id"], {})[item["arm"]] = item
    missing = sorted(pair for pair, values in pairs.items() if set(values) != {"raw_full_file", "aether_patch"})
    if missing:
        raise ValueError(f"Every pair must contain raw_full_file and aether_patch records; missing={missing}")

    both_pass = aether_only = raw_only = neither = 0
    for values in pairs.values():
        raw = bool(values["raw_full_file"]["task_success"])
        aether = bool(values["aether_patch"]["task_success"])
        if raw and aether:
            both_pass += 1
        elif aether:
            aether_only += 1
        elif raw:
            raw_only += 1
        else:
            neither += 1
    return {
        "pairs": len(pairs),
        "both_pass": both_pass,
        "aether_only_pass": aether_only,
        "raw_only_pass": raw_only,
        "neither_pass": neither,
    }


def quality_efficiency_gate(efficiency: dict[str, Any], paired: dict[str, Any]) -> dict[str, Any]:
    success_delta = efficiency["aether_success_delta_percentage_points"]
    token_savings = efficiency["aether_output_token_savings_pct"]
    raw_only = paired["raw_only_pass"]
    passed = success_delta >= 0 and token_savings >= 20 and raw_only == 0
    return {
        "passed": passed,
        "criteria": {
            "aether_success_delta_percentage_points_gte": 0,
            "aether_output_token_savings_pct_gte": 20,
            "raw_only_pass_eq": 0,
        },
        "observed": {
            "aether_success_delta_percentage_points": success_delta,
            "aether_output_token_savings_pct": token_savings,
            "raw_only_pass": raw_only,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    raw = report["by_arm"]["raw_full_file"]
    aether = report["by_arm"]["aether_patch"]
    efficiency = report["efficiency"]
    paired = report["paired_success"]
    gate = report["quality_efficiency_gate"]
    lines = [
        "# Unseen Agent A/B Report",
        "",
        f"- Tasks: `{report['task_count']}`; trials: `{report['trials']}`.",
        f"- Raw full-file success: `{raw['successes']}/{raw['records']}` (`{raw['success_rate']}`).",
        f"- Aether patch success: `{aether['successes']}/{aether['records']}` (`{aether['success_rate']}`).",
        f"- Success delta: `{efficiency['aether_success_delta_percentage_points']}` percentage points.",
        f"- Aether output-token savings: `{efficiency['aether_output_token_savings_pct']}%`.",
        f"- Aether output-byte savings: `{efficiency['aether_output_byte_savings_pct']}%`.",
        f"- Paired outcomes: both pass `{paired['both_pass']}`, Aether-only `{paired['aether_only_pass']}`, raw-only `{paired['raw_only_pass']}`, neither `{paired['neither_pass']}`.",
        f"- Quality-efficiency gate passed: `{gate['passed']}`.",
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
