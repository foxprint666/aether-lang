#!/usr/bin/env python
"""Publish local RAG A/B evidence as JSON and Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    report = {
        "report_version": "local-rag-ab-evidence-v1",
        "experiment_id": payload.get("experiment_id"),
        "commit_sha": payload.get("commit_sha"),
        "questions": payload["questions"],
        "summary": payload["summary"],
        "limitations": [
            "The chatbot is deterministic and extractive; it does not call a live LLM.",
            "Token counts are local lexical estimates, not provider billing telemetry.",
            "The corpus is a focused local benchmark corpus, not yet a broad external RAG suite.",
        ],
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def markdown(report: dict) -> str:
    summary = report["summary"]
    raw = summary["raw"]
    aether = summary["aether"]
    eff = summary["efficiency"]
    lines = [
        "# Local RAG A/B Report",
        "",
        f"- Questions: `{report['questions']}`.",
        f"- Raw quality: `{raw['quality_score']}`.",
        f"- Aether quality: `{aether['quality_score']}`.",
        f"- Quality delta: `{eff['quality_delta']}`.",
        f"- Raw context tokens: `{raw['context_tokens']}`.",
        f"- Aether context tokens: `{aether['context_tokens']}`.",
        f"- Context token savings: `{eff['context_token_savings_pct']}%`.",
        f"- Total token savings: `{eff['total_token_savings_pct']}%`.",
        f"- Latency savings: `{eff['latency_savings_pct']}%`.",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())

