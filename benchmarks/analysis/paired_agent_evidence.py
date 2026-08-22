#!/usr/bin/env python
"""Analyze matched blind Aether-patch versus full-file generation evidence."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ARMS = ("aether_patch", "full_file")


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
    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in records:
        groups[item["pair_id"]][item["artifact_format"]] = item
    if any(set(values) != set(ARMS) for values in groups.values()):
        raise ValueError("Every pair_id must contain exactly both artifact arms")

    patch_only = full_only = both = neither = 0
    for values in groups.values():
        patch = bool(values["aether_patch"]["task_success"])
        full = bool(values["full_file"]["task_success"])
        if patch and full:
            both += 1
        elif patch:
            patch_only += 1
        elif full:
            full_only += 1
        else:
            neither += 1
    by_arm = {arm: arm_summary(records, arm) for arm in ARMS}
    delta = by_arm["aether_patch"]["success_rate"] - by_arm["full_file"]["success_rate"]
    patch_tokens = by_arm["aether_patch"]["output_tokens"]
    full_tokens = by_arm["full_file"]["output_tokens"]
    repositories = sorted({item["repository"] for item in records})
    tasks = sorted({item["task_id"] for item in records})
    return {
        "report_version": "paired-blind-agent-v1",
        "evidence": {
            "pairs": len(groups),
            "tasks": len(tasks),
            "repositories": repositories,
            "trials": sorted({item["trial"] for item in records}),
            "baseline_original_passes": payload.get("baseline_original_passes", []),
            "prompt_core_sha256": payload.get("prompt_core_sha256"),
        },
        "by_arm": by_arm,
        "paired_success": {
            "aether_minus_full_percentage_points": round(delta * 100, 6),
            "bootstrap_95pct_percentage_points": bootstrap_ci(groups),
            "both_pass": both,
            "aether_only_pass": patch_only,
            "full_file_only_pass": full_only,
            "neither_pass": neither,
            "mcnemar_exact_two_sided_p": mcnemar_exact(patch_only, full_only),
        },
        "efficiency": {
            "aether_output_token_savings_pct": round((full_tokens - patch_tokens) / full_tokens * 100, 6),
            "aether_output_byte_savings_pct": round(
                (by_arm["full_file"]["output_bytes"] - by_arm["aether_patch"]["output_bytes"])
                / by_arm["full_file"]["output_bytes"] * 100, 6
            ),
        },
        "limitations": [
            "Generation used fresh stateless Codex subagents with prompt-enforced source-only restrictions, not OS-enforced filesystem denial.",
            "Offline token counts are tiktoken estimates; provider generation latency, retries, and monetary cost were not available.",
            f"The {len(tasks)} tasks come from {len(repositories)} pinned repositories and are not representative of all coding-agent workloads.",
            "These tasks became public with this evidence and must not be reused as unseen tasks.",
        ],
    }


def arm_summary(records: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    selected = [item for item in records if item["artifact_format"] == arm]
    return {
        "records": len(selected),
        "successes": sum(item["task_success"] for item in selected),
        "success_rate": round(sum(item["task_success"] for item in selected) / len(selected), 6),
        "format_valid_rate": stage_rate(selected, "format_valid"),
        "applicable_rate": stage_rate(selected, "applicable"),
        "syntax_valid_rate": stage_rate(selected, "syntax_valid"),
        "hidden_test_pass_rate": stage_rate(selected, "hidden_test_pass"),
        "output_tokens": sum(item["estimated_output_tokens"] for item in selected),
        "output_bytes": sum(item["output_bytes"] for item in selected),
        "mean_application_time_ms": round(statistics.mean(item["application_time_ms"] for item in selected), 6),
        "mean_verification_time_ms": round(statistics.mean(
            item["verification_time_ms"] for item in selected if item["verification_time_ms"] is not None
        ), 6),
    }


def stage_rate(records: list[dict[str, Any]], field: str) -> float:
    return round(sum(bool(item[field]) for item in records) / len(records), 6)


def mcnemar_exact(left_only: int, right_only: int) -> float:
    total = left_only + right_only
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, index) for index in range(min(left_only, right_only) + 1)) / (2 ** total)
    return round(min(1.0, 2 * tail), 6)


def bootstrap_ci(groups: dict[str, dict[str, dict[str, Any]]]) -> list[float]:
    by_task: dict[str, list[float]] = defaultdict(list)
    for pair_id, values in groups.items():
        task = pair_id.split(":", 1)[0]
        by_task[task].append(
            float(values["aether_patch"]["task_success"])
            - float(values["full_file"]["task_success"])
        )
    tasks = sorted(by_task)
    rng = random.Random(20260816)
    samples = []
    for _ in range(10000):
        selected = [rng.choice(tasks) for _ in tasks]
        values = [value for task in selected for value in by_task[task]]
        samples.append(statistics.mean(values) * 100)
    samples.sort()
    return [round(samples[249], 6), round(samples[9749], 6)]


def markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    patch = report["by_arm"]["aether_patch"]
    full = report["by_arm"]["full_file"]
    paired = report["paired_success"]
    efficiency = report["efficiency"]
    lines = [
        "# Paired Blind Agent-Generation Report", "",
        f"- Matched pairs: `{evidence['pairs']}` across `{evidence['tasks']}` tasks and `{len(evidence['trials'])}` trials.",
        f"- Aether patches: `{patch['successes']}/{patch['records']}` (`{patch['success_rate']}`).",
        f"- Full files: `{full['successes']}/{full['records']}` (`{full['success_rate']}`).",
        f"- Success difference: `{paired['aether_minus_full_percentage_points']}` percentage points; task-clustered bootstrap 95% interval `{paired['bootstrap_95pct_percentage_points']}`.",
        f"- Discordant pairs: Aether-only `{paired['aether_only_pass']}`, full-file-only `{paired['full_file_only_pass']}`; exact McNemar p `{paired['mcnemar_exact_two_sided_p']}`.",
        f"- Aether output-token savings: `{efficiency['aether_output_token_savings_pct']}%`; byte savings: `{efficiency['aether_output_byte_savings_pct']}%`.",
        f"- Original revisions already passing hidden checks: `{len(evidence['baseline_original_passes'])}`.",
        "", "## Limitations", "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
