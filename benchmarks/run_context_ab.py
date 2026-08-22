#!/usr/bin/env python
"""Measure raw-source versus graph-scoped context packets for agent tasks."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import run as base
from context.graph_context import build_graph_packet, build_raw_packet, canonical_hash
from run_paired_agent import load_sources, make_task


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "tasks" / "paired_agent_unseen.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--allow-network-repos", action="store_true")
    args = parser.parse_args()
    base.CURRENT_ARGS = argparse.Namespace(allow_network_repos=args.allow_network_repos)

    manifest = load_object(args.manifest)
    task_values = manifest.get("tasks")
    if not isinstance(task_values, list) or not task_values:
        raise ValueError("Context A/B manifest must contain tasks")
    tasks = {item["task_id"]: make_task(item) for item in task_values}
    sources = load_sources(tasks, task_values)

    records = [measure_task(item, sources[item["source_id"]], args.experiment_id) for item in task_values]
    output = {
        "report_version": "context-ab-v1",
        "experiment_id": args.experiment_id,
        "commit_sha": base.git_commit_sha(),
        "manifest_sha256": file_hash(args.manifest),
        "records": records,
        "summary": summary(records),
    }
    raw = ROOT / "benchmarks" / "results" / "raw" / f"{args.experiment_id}.json"
    processed = ROOT / "benchmarks" / "results" / "processed" / f"{args.experiment_id}.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    processed.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(processed, records)
    print(f"Wrote raw results: {raw.relative_to(ROOT)}")
    print(f"Wrote CSV results: {processed.relative_to(ROOT)}")
    print(json.dumps(output["summary"], indent=2, sort_keys=True))
    return 0


def measure_task(task: dict[str, Any], source: str, experiment_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    raw_packet = build_raw_packet(task, source)
    raw_build_ms = elapsed_ms(started)
    started = time.perf_counter()
    graph_packet = build_graph_packet(task, source)
    graph_build_ms = elapsed_ms(started)
    raw_text = json.dumps(raw_packet, sort_keys=True)
    graph_text = json.dumps(graph_packet, sort_keys=True)
    raw_tokens = base.count_tokens(raw_text)
    graph_tokens = base.count_tokens(graph_text)
    selected = graph_packet["selected_symbols"]
    selected_names = [item["name"] for item in selected]
    return {
        "experiment_id": experiment_id,
        "task_id": task["task_id"],
        "language": task["language"],
        "repository": task["repository"],
        "source_file": task["source_file"],
        "raw_context_sha256": canonical_hash(raw_packet),
        "graph_context_sha256": canonical_hash(graph_packet),
        "source_bytes": len(source.encode("utf-8")),
        "raw_context_bytes": len(raw_text.encode("utf-8")),
        "graph_context_bytes": len(graph_text.encode("utf-8")),
        "raw_context_tokens": raw_tokens,
        "graph_context_tokens": graph_tokens,
        "context_token_savings_pct": pct(raw_tokens, graph_tokens),
        "context_byte_savings_pct": pct(len(raw_text.encode("utf-8")), len(graph_text.encode("utf-8"))),
        "raw_build_time_ms": round(raw_build_ms, 6),
        "graph_build_time_ms": round(graph_build_ms, 6),
        "graph_symbol_count": graph_packet["graph"]["node_count"],
        "graph_edge_count": graph_packet["graph"]["edge_count"],
        "selected_symbol_count": len(selected),
        "selected_symbols": selected_names,
        "target_mentioned_in_selected_symbols": target_hit(task["description"], selected_names),
        "token_estimator": base.token_estimator_name(),
    }


def target_hit(description: str, selected_names: list[str]) -> bool:
    lowered = description.lower()
    return any(name.lower() in lowered for name in selected_names)


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        "repositories": sorted({item["repository"] for item in records}),
        "languages": sorted({item["language"] for item in records}),
        "raw_context_tokens": sum(item["raw_context_tokens"] for item in records),
        "graph_context_tokens": sum(item["graph_context_tokens"] for item in records),
        "context_token_savings_pct": pct(
            sum(item["raw_context_tokens"] for item in records),
            sum(item["graph_context_tokens"] for item in records),
        ),
        "context_byte_savings_pct": pct(
            sum(item["raw_context_bytes"] for item in records),
            sum(item["graph_context_bytes"] for item in records),
        ),
        "target_hit_rate": round(
            sum(1 for item in records if item["target_mentioned_in_selected_symbols"]) / len(records),
            6,
        ),
        "mean_graph_build_time_ms": round(
            sum(item["graph_build_time_ms"] for item in records) / len(records),
            6,
        ),
    }


def pct(left: int | float, right: int | float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected object JSON in {path}")
    return value


def file_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


if __name__ == "__main__":
    raise SystemExit(main())
