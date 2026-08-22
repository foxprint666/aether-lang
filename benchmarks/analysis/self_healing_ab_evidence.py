#!/usr/bin/env python
"""Publish self-healing loop A/B evidence."""

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
    summary = payload["summary"]
    raw = summary["by_arm"]["raw_healing"]
    aether = summary["by_arm"]["aether_healing"]
    gate = {
        "passed": (
            summary["aether_repair_success_delta_percentage_points"] >= 0
            and summary["aether_output_token_savings_pct"] >= 20
            and aether["corruptions"] == 0
            and aether["safety_success_rate"] == 1.0
        ),
        "criteria": {
            "repair_success_delta_gte": 0,
            "output_token_savings_pct_gte": 20,
            "aether_corruptions_eq": 0,
            "aether_safety_success_rate_eq": 1.0,
        },
        "observed": {
            "repair_success_delta": summary["aether_repair_success_delta_percentage_points"],
            "output_token_savings_pct": summary["aether_output_token_savings_pct"],
            "aether_corruptions": aether["corruptions"],
            "aether_safety_success_rate": aether["safety_success_rate"],
        },
    }
    return {
        "report_version": "self-healing-ab-evidence-v1",
        "experiment_id": payload["experiment_id"],
        "commit_sha": payload.get("commit_sha"),
        "task_count": payload["task_count"],
        "trials": payload["trials"],
        "baseline_original_failures": payload["baseline_original_failures"],
        "by_arm": summary["by_arm"],
        "efficiency": {
            "aether_output_token_savings_pct": summary["aether_output_token_savings_pct"],
            "aether_output_byte_savings_pct": summary["aether_output_byte_savings_pct"],
            "aether_repair_success_delta_percentage_points": summary["aether_repair_success_delta_percentage_points"],
            "aether_corruption_reduction": summary["aether_corruption_reduction"],
        },
        "self_healing_gate": gate,
        "records": [
            {
                "task_id": item["task_id"],
                "arm": item["arm"],
                "repair_success": item["repair_success"],
                "safety_success": item["safety_success"],
                "repository_corrupted_on_failure": item["repository_corrupted_on_failure"],
                "output_tokens": item["output_tokens"],
                "error_type": item["error_type"],
            }
            for item in payload["records"]
        ],
        "limitations": payload.get("limitations", []),
        "interpretation": [
            "Valid repair tasks measure whether the healing loop can restore expected behavior.",
            "The invalid repair task measures whether failed autonomous mutation leaves the repository corrupted.",
            "Aether's advantage should be judged by repair quality plus corruption prevention plus token efficiency, not token savings alone.",
        ],
    }


def markdown(report: dict[str, Any]) -> str:
    raw = report["by_arm"]["raw_healing"]
    aether = report["by_arm"]["aether_healing"]
    efficiency = report["efficiency"]
    gate = report["self_healing_gate"]
    lines = [
        "# Self-Healing Loop A/B Report",
        "",
        f"- Tasks: `{report['task_count']}`; trials: `{report['trials']}`.",
        f"- Baseline original failures: `{len(report['baseline_original_failures'])}`.",
        f"- Raw repair success rate: `{raw['repair_success_rate']}`.",
        f"- Aether repair success rate: `{aether['repair_success_rate']}`.",
        f"- Raw safety success rate: `{raw['safety_success_rate']}`.",
        f"- Aether safety success rate: `{aether['safety_success_rate']}`.",
        f"- Raw corruptions after failed attempts: `{raw['corruptions']}`.",
        f"- Aether corruptions after failed attempts: `{aether['corruptions']}`.",
        f"- Aether corruption reduction: `{efficiency['aether_corruption_reduction']}`.",
        f"- Aether output-token savings: `{efficiency['aether_output_token_savings_pct']}%`.",
        f"- Aether output-byte savings: `{efficiency['aether_output_byte_savings_pct']}%`.",
        f"- Self-healing gate passed: `{gate['passed']}`.",
        f"- Raw mean total task time: `{raw['mean_total_task_time_ms']} ms`.",
        f"- Aether mean total task time: `{aether['mean_total_task_time_ms']} ms`.",
        "",
        "## Interpretation",
        "",
    ]
    lines.extend(f"- {item}" for item in report["interpretation"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
