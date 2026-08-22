#!/usr/bin/env python
"""Publish evidence for raw-source versus graph-scoped context packets."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
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
    return {
        "report_version": "context-ab-v1",
        "experiment_id": payload.get("experiment_id"),
        "commit_sha": payload.get("commit_sha"),
        "evidence": {
            "records": len(records),
            "repositories": sorted({item["repository"] for item in records}),
            "languages": sorted({item["language"] for item in records}),
            "target_hit_rate": rate(sum(bool(item["target_mentioned_in_selected_symbols"]) for item in records), len(records)),
        },
        "context_efficiency": {
            "raw_context_tokens": total(records, "raw_context_tokens"),
            "graph_context_tokens": total(records, "graph_context_tokens"),
            "context_token_savings_pct": pct(total(records, "raw_context_tokens"), total(records, "graph_context_tokens")),
            "raw_context_bytes": total(records, "raw_context_bytes"),
            "graph_context_bytes": total(records, "graph_context_bytes"),
            "context_byte_savings_pct": pct(total(records, "raw_context_bytes"), total(records, "graph_context_bytes")),
            "mean_graph_build_time_ms": round(sum(float(item["graph_build_time_ms"]) for item in records) / len(records), 6),
        },
        "by_language": grouped(records, "language"),
        "by_repository": grouped(records, "repository"),
        "limitations": [
            "This measures context packets and target selection, not a live agent's correctness from graph-scoped context.",
            "The graph extractor is lightweight and local; it is not yet a full Graphify integration.",
            "The task set is seven focused single-file tasks and does not cover whole-feature construction.",
        ],
    }


def grouped(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record[key])].append(record)
    return {
        name: {
            "records": len(items),
            "context_token_savings_pct": pct(total(items, "raw_context_tokens"), total(items, "graph_context_tokens")),
            "target_hit_rate": rate(sum(bool(item["target_mentioned_in_selected_symbols"]) for item in items), len(items)),
            "mean_graph_build_time_ms": round(sum(float(item["graph_build_time_ms"]) for item in items) / len(items), 6),
        }
        for name, items in sorted(groups.items())
    }


def total(records: list[dict[str, Any]], field: str) -> int:
    return int(sum(int(item[field]) for item in records))


def pct(left: int | float, right: int | float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    context = report["context_efficiency"]
    lines = [
        "# Context A/B Evidence Report",
        "",
        f"- Records: `{evidence['records']}` across `{len(evidence['repositories'])}` repositories.",
        f"- Raw context tokens: `{context['raw_context_tokens']}`.",
        f"- Graph-scoped context tokens: `{context['graph_context_tokens']}`.",
        f"- Context-token savings: `{context['context_token_savings_pct']}%`.",
        f"- Context-byte savings: `{context['context_byte_savings_pct']}%`.",
        f"- Target-symbol hit rate: `{evidence['target_hit_rate']}`.",
        f"- Mean graph build time: `{context['mean_graph_build_time_ms']} ms`.",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
