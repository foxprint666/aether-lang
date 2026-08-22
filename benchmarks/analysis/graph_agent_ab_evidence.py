#!/usr/bin/env python
"""Analyze graph-scoped versus raw-source agent patch generation evidence."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


STRATEGIES = ("raw_source", "graph_scoped")


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
    for record in records:
        groups[record["pair_id"]][record["context_strategy"]] = record
    if any(set(value) != set(STRATEGIES) for value in groups.values()):
        raise ValueError("Every pair_id must contain raw_source and graph_scoped records")
    raw_only = graph_only = both = neither = 0
    for value in groups.values():
        raw = bool(value["raw_source"]["task_success"])
        graph = bool(value["graph_scoped"]["task_success"])
        if raw and graph:
            both += 1
        elif raw:
            raw_only += 1
        elif graph:
            graph_only += 1
        else:
            neither += 1
    raw = strategy_summary(records, "raw_source")
    graph = strategy_summary(records, "graph_scoped")
    return {
        "report_version": "graph-agent-ab-v1",
        "experiment_id": payload.get("experiment_id"),
        "commit_sha": payload.get("commit_sha"),
        "evidence": {
            "pairs": len(groups),
            "tasks": len({record["task_id"] for record in records}),
            "trials": sorted({record["trial"] for record in records}),
            "baseline_original_passes": payload.get("baseline_original_passes", []),
        },
        "by_strategy": {
            "raw_source": raw,
            "graph_scoped": graph,
        },
        "paired_success": {
            "graph_minus_raw_percentage_points": round((graph["success_rate"] - raw["success_rate"]) * 100, 6),
            "both_pass": both,
            "raw_only_pass": raw_only,
            "graph_only_pass": graph_only,
            "neither_pass": neither,
            "mcnemar_exact_two_sided_p": mcnemar_exact(raw_only, graph_only),
        },
        "efficiency": {
            "graph_context_input_token_savings_pct": pct(raw["context_input_tokens"], graph["context_input_tokens"]),
            "graph_output_token_delta_pct": pct(raw["estimated_output_tokens"], graph["estimated_output_tokens"]),
            "graph_total_token_savings_pct": pct(raw["total_estimated_tokens"], graph["total_estimated_tokens"]),
        },
        "limitations": [
            "Graph-scoped generation used fresh stateless Codex subagents with prompt-enforced source-only restrictions, not OS-enforced filesystem denial.",
            "Raw-source comparison reuses the prior paired blind patch trials; graph-scoped trials are newly generated.",
            "Token counts are offline tiktoken estimates, not provider billing telemetry.",
        ],
    }


def strategy_summary(records: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    selected = [record for record in records if record["context_strategy"] == strategy]
    successes = sum(bool(record["task_success"]) for record in selected)
    context = sum(int(record["context_input_tokens"]) for record in selected)
    output = sum(int(record["estimated_output_tokens"]) for record in selected)
    return {
        "records": len(selected),
        "successes": successes,
        "success_rate": round(successes / len(selected), 6),
        "format_valid_rate": rate(sum(bool(record["format_valid"]) for record in selected), len(selected)),
        "applicable_rate": rate(sum(bool(record["applicable"]) for record in selected), len(selected)),
        "hidden_test_pass_rate": rate(sum(bool(record["hidden_test_pass"]) for record in selected), len(selected)),
        "context_input_tokens": context,
        "estimated_output_tokens": output,
        "total_estimated_tokens": context + output,
    }


def mcnemar_exact(left_only: int, right_only: int) -> float:
    total = left_only + right_only
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, index) for index in range(min(left_only, right_only) + 1)) / (2 ** total)
    return round(min(1.0, 2 * tail), 6)


def pct(left: int | float, right: int | float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def markdown(report: dict[str, Any]) -> str:
    raw = report["by_strategy"]["raw_source"]
    graph = report["by_strategy"]["graph_scoped"]
    paired = report["paired_success"]
    eff = report["efficiency"]
    lines = [
        "# Graph-Scoped Agent A/B Report",
        "",
        f"- Matched pairs: `{report['evidence']['pairs']}` across `{report['evidence']['tasks']}` tasks.",
        f"- Raw-source patches: `{raw['successes']}/{raw['records']}` (`{raw['success_rate']}`).",
        f"- Graph-scoped patches: `{graph['successes']}/{graph['records']}` (`{graph['success_rate']}`).",
        f"- Success difference: `{paired['graph_minus_raw_percentage_points']}` percentage points.",
        f"- Discordant pairs: raw-only `{paired['raw_only_pass']}`, graph-only `{paired['graph_only_pass']}`; exact McNemar p `{paired['mcnemar_exact_two_sided_p']}`.",
        f"- Graph context-input token savings: `{eff['graph_context_input_token_savings_pct']}%`.",
        f"- Graph total-token savings: `{eff['graph_total_token_savings_pct']}%`.",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
