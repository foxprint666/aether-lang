#!/usr/bin/env python
"""Grade raw-source patch trials against graph-scoped patch trials."""

from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import run as base
from agents.paired_blind_protocol import PATCH_ARM, canonical_hash
from context.graph_context import build_graph_packet, build_raw_packet
from run_paired_agent import baseline_failures, grade, load_sources, make_task


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "tasks" / "paired_agent_unseen.json"
DEFAULT_RAW_TRIAL_DIR = ROOT / "benchmarks" / "agents" / "paired_blind_trials"
DEFAULT_GRAPH_TRIAL_DIR = ROOT / "benchmarks" / "agents" / "graph_context_trials"
STRATEGIES = ("raw_source", "graph_scoped")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-trials-dir", type=Path, default=DEFAULT_RAW_TRIAL_DIR)
    parser.add_argument("--graph-trials-dir", type=Path, default=DEFAULT_GRAPH_TRIAL_DIR)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--repair-common-schema-errors", action="store_true")
    parser.add_argument("--allow-network-repos", action="store_true")
    parser.add_argument("--keep-workdirs", action="store_true")
    args = parser.parse_args()
    base.CURRENT_ARGS = argparse.Namespace(allow_network_repos=args.allow_network_repos)

    manifest = load_object(args.manifest)
    task_values = manifest.get("tasks")
    if not isinstance(task_values, list) or not task_values:
        raise ValueError("Graph agent A/B manifest must contain tasks")
    tasks = {item["task_id"]: make_task(item) for item in task_values}
    task_meta = {item["task_id"]: item for item in task_values}
    sources = load_sources(tasks, task_values)
    packets = build_packets(task_values, sources)
    generations = load_generations(args.raw_trials_dir, args.graph_trials_dir, tasks, args.trials)
    baseline = baseline_failures(tasks, args.keep_workdirs)

    records: list[dict[str, Any]] = []
    for generation in generations:
        task = tasks[generation["task"]]
        strategy = generation["context_strategy"]
        packet = packets[strategy][generation["task"]]
        packet_text = json.dumps(packet, sort_keys=True)
        grade_generation = generation
        repair = {"enabled": False, "changed": False, "original_artifact_sha256": None}
        if args.repair_common_schema_errors and strategy == "graph_scoped":
            repaired = repair_common_schema_errors(generation["artifact"], packet)
            repair = {
                "enabled": True,
                "changed": repaired != generation["artifact"],
                "original_artifact_sha256": canonical_hash(generation["artifact"]),
            }
            grade_generation = {**generation, "artifact": repaired}
        record = grade(
            task,
            {"arm": PATCH_ARM, **grade_generation},
            core_hash=canonical_hash(packet),
            core_tokens=base.count_tokens(packet_text),
            experiment_id=args.experiment_id,
            keep_workdir=args.keep_workdirs,
        )
        record.update({
            "context_strategy": strategy,
            "context_packet_sha256": canonical_hash(packet),
            "context_input_tokens": base.count_tokens(packet_text),
            "context_input_bytes": len(packet_text.encode("utf-8")),
            "context_source_id": task_meta[generation["task"]]["source_id"],
            "schema_repair_enabled": repair["enabled"],
            "schema_repair_changed": repair["changed"],
            "original_artifact_sha256": repair["original_artifact_sha256"],
        })
        records.append(record)

    output = {
        "report_version": "graph-agent-ab-v1",
        "experiment_id": args.experiment_id,
        "commit_sha": base.git_commit_sha(),
        "manifest_sha256": file_hash(args.manifest),
        "trials": args.trials,
        "schema_repair_enabled": args.repair_common_schema_errors,
        "baseline_original_passes": baseline,
        "records": records,
        "summary": summary(records, baseline),
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


def build_packets(task_values: list[dict[str, Any]], sources: dict[str, str]) -> dict[str, dict[str, dict[str, Any]]]:
    packets = {"raw_source": {}, "graph_scoped": {}}
    for task in task_values:
        source = sources[task["source_id"]]
        packets["raw_source"][task["task_id"]] = build_raw_packet(task, source)
        packets["graph_scoped"][task["task_id"]] = build_graph_packet(task, source)
    return packets


def load_generations(
    raw_dir: Path,
    graph_dir: Path,
    tasks: dict[str, base.BenchmarkTask],
    trials: int,
) -> list[dict[str, Any]]:
    records = []
    seen: set[tuple[str, int, str]] = set()
    for path, strategy in [
        *[(item, "raw_source") for item in sorted(raw_dir.glob("patch-trial-*.json"))],
        *[(item, "graph_scoped") for item in sorted(graph_dir.glob("graph-patch-trial-*.json"))],
    ]:
        path = path.resolve()
        raw = path.read_bytes()
        payload = json.loads(raw)
        trial = payload.get("trial")
        values = payload.get("records")
        if payload.get("arm") != PATCH_ARM or not isinstance(trial, int) or not isinstance(values, list):
            raise ValueError(f"Invalid graph agent trial envelope: {path}")
        if trial < 1 or trial > trials:
            continue
        for value in values:
            task_id = value.get("task") if isinstance(value, dict) else None
            key = (str(task_id), trial, strategy)
            if task_id not in tasks or key in seen:
                raise ValueError(f"Unknown or duplicate graph agent generation {key} in {path}")
            patch = value.get("patch")
            if not isinstance(patch, dict):
                raise ValueError(f"Patch artifact missing for {key}")
            seen.add(key)
            records.append({
                "task": task_id,
                "trial": trial,
                "context_strategy": strategy,
                "artifact": patch,
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_response_file": str(path.relative_to(ROOT)).replace("\\", "/"),
            })
    expected = {
        (task, trial, strategy)
        for task in tasks
        for trial in range(1, trials + 1)
        for strategy in STRATEGIES
    }
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise ValueError(f"Graph agent generation coverage mismatch; missing={missing}, extra={extra}")
    return sorted(records, key=lambda item: (item["trial"], item["task"], item["context_strategy"]))


def repair_common_schema_errors(artifact: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    patch = {
        key: copy.deepcopy(value)
        for key, value in artifact.items()
        if key in {"schema_version", "patch_id", "action", "target", "changes", "metadata"}
    }
    target = patch.setdefault("target", {})
    changes = patch.get("changes")
    if patch.get("action") == "replace_body":
        symbol_type = infer_symbol_type(target.get("symbol"), packet)
        patch["action"] = "modify_class" if symbol_type == "class" else "modify_function"
    if isinstance(changes, list) and changes and isinstance(changes[0], dict):
        first = dict(changes[0])
        if "operation" not in first and isinstance(first.get("type"), str):
            first["operation"] = first.pop("type")
        patch["changes"] = first
        changes = patch["changes"]
    if isinstance(changes, dict):
        if "operation" not in changes and isinstance(changes.get("type"), str):
            changes["operation"] = changes.pop("type")
        if "operation" not in changes and isinstance(changes.get("payload"), str):
            changes["operation"] = "replace_body"
    if isinstance(target, dict) and not isinstance(target.get("symbol_type"), str):
        target["symbol_type"] = infer_symbol_type(target.get("symbol"), packet)
    return patch


def infer_symbol_type(symbol: Any, packet: dict[str, Any]) -> str:
    if isinstance(symbol, str):
        for item in packet.get("selected_symbols", []):
            if isinstance(item, dict) and item.get("name") == symbol and isinstance(item.get("kind"), str):
                return str(item["kind"])
    language = packet.get("language")
    return "function" if language == "python" else "method"


def summary(records: list[dict[str, Any]], baseline: list[str]) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        selected = [item for item in records if item["context_strategy"] == strategy]
        by_strategy[strategy] = {
            "records": len(selected),
            "successes": sum(bool(item["task_success"]) for item in selected),
            "context_input_tokens": sum(int(item["context_input_tokens"]) for item in selected),
            "estimated_output_tokens": sum(int(item["estimated_output_tokens"]) for item in selected),
        }
    return {
        "records": len(records),
        "baseline_original_passes": baseline,
        "by_strategy": by_strategy,
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(records[0]) if records else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected object JSON in {path}")
    return value


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
