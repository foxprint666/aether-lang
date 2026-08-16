#!/usr/bin/env python
"""Summarize matched external-repository correctness and efficiency evidence."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


MODES = ("control", "state", "aether", "hybrid")


def main() -> int:
    args = parse_args()
    records, experiments = load_records(args.raw_results)
    external = [record for record in records if record.get("category") == "external_repository"]
    matched = matched_mode_records(external)
    triples = [item for item in matched.values() if {"control", "state", "aether"} <= set(item)]
    report = {
        "report_version": "external-efficiency-v1",
        "experiments": experiments,
        "evidence": evidence_summary(external),
        "by_mode": grouped_summary(external, mode_of),
        "by_repository": grouped_summary(external, lambda record: record.get("repository")),
        "by_language": grouped_summary(external, lambda record: record.get("language")),
        "by_verification_level": grouped_summary(external, verification_level),
        "matched_triples": {
            "n": len(triples),
            "success_rate": {
                mode: rate(sum(bool(item[mode].get("task_success")) for item in triples), len(triples))
                for mode in ("control", "state", "aether")
            },
        },
        "comparisons": {
            "state_vs_control": compare(triples, "control", "state"),
            "aether_vs_control": compare(triples, "control", "aether"),
            "aether_vs_state": compare(triples, "state", "aether"),
            "hybrid_vs_control": compare(triples, "control", "hybrid"),
        },
        "hybrid": hybrid_summary(external),
        "safety": safety_summary(external),
        "source_size_effect": source_size_effect(triples),
        "limitations": [
            "Token values are offline tokenizer estimates, not provider billing telemetry.",
            "Generation latency and model retries are absent because these tasks use deterministic reference patches.",
            "Control execution measures writing an already-generated complete file; model generation time is not fabricated.",
            "Raw execution time excludes repository checkout and verification; edit_to_verified_time_ms includes syntax and declared task verification.",
            "Behavior-level verification is reported separately from syntax-only verification.",
        ],
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(rendered, end="")
    return 0 if external and all(record.get("task_success") for record in external) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze external repository benchmark efficiency.")
    parser.add_argument("raw_results", nargs="+", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def load_records(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    experiments: list[str] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
        if payload.get("experiment_id"):
            experiments.append(str(payload["experiment_id"]))
    return records, experiments


def evidence_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        "successful_records": sum(bool(record.get("task_success")) for record in records),
        "success_rate": rate(sum(bool(record.get("task_success")) for record in records), len(records)),
        "repositories": sorted({str(record.get("repository")) for record in records}),
        "repository_count": len({record.get("repository") for record in records}),
        "tasks": sorted({str(record.get("task_id")) for record in records}),
        "task_count": len({record.get("task_id") for record in records}),
        "languages": sorted({str(record.get("language")) for record in records}),
        "modes": sorted({mode_of(record) for record in records}),
        "verification_levels": dict(sorted(Counter(verification_level(record) for record in records).items())),
        "token_estimators": sorted(
            {str(record.get("token_estimator")) for record in records if record.get("token_estimator")}
        ),
    }


def grouped_summary(
    records: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(key(record))].append(record)
    return {name: record_summary(items) for name, items in sorted(groups.items())}


def record_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = sum(bool(record.get("task_success")) for record in records)
    return {
        "records": len(records),
        "success_rate": rate(successful, len(records)),
        "execution_time_ms": numeric(record.get("execution_time_ms") for record in records),
        "verification_time_ms": numeric(record.get("verification_time_ms") for record in records),
        "edit_to_verified_time_ms": numeric(record.get("edit_to_verified_time_ms") for record in records),
        "repository_setup_time_ms": numeric(record.get("repository_setup_time_ms") for record in records),
        "estimated_input_tokens": numeric(record.get("estimated_input_tokens") for record in records),
        "estimated_output_tokens": numeric(record.get("estimated_output_tokens") for record in records),
        "output_size_bytes": numeric(record.get("output_size_bytes") for record in records),
    }


def matched_mode_records(records: list[dict[str, Any]]) -> dict[tuple[str, str, Any], dict[str, dict[str, Any]]]:
    matched: dict[tuple[str, str, Any], dict[str, dict[str, Any]]] = {}
    for record in records:
        mode = mode_of(record)
        if mode not in MODES:
            continue
        key = (
            str(record.get("experiment_id")),
            str(record.get("task_id")),
            record.get("configuration", {}).get("trial"),
        )
        matched.setdefault(key, {})[mode] = record
    return matched


def compare(
    triples: list[dict[str, dict[str, Any]]],
    left_mode: str,
    right_mode: str,
) -> dict[str, Any]:
    pairs = [(item[left_mode], item[right_mode]) for item in triples]
    return {
        "n": len(pairs),
        "left_mode": left_mode,
        "right_mode": right_mode,
        "success": {
            "left_rate": rate(sum(bool(left.get("task_success")) for left, _ in pairs), len(pairs)),
            "right_rate": rate(sum(bool(right.get("task_success")) for _, right in pairs), len(pairs)),
        },
        "estimated_output_tokens": compare_additive(pairs, lambda record: record.get("estimated_output_tokens")),
        "estimated_total_tokens": compare_additive(pairs, estimated_total_tokens),
        "emitted_bytes": compare_additive(pairs, lambda record: record.get("output_size_bytes")),
        "execution_time_ms": compare_timing(pairs, "execution_time_ms"),
        "edit_to_verified_time_ms": compare_timing(pairs, "edit_to_verified_time_ms"),
        "verification_time_ms": compare_timing(pairs, "verification_time_ms"),
    }


def compare_additive(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    value: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    values = [
        (float(value(left)), float(value(right)))
        for left, right in pairs
        if is_number(value(left)) and is_number(value(right))
    ]
    left_total = sum(left for left, _ in values)
    right_total = sum(right for _, right in values)
    paired_savings = [(left - right) / left * 100 for left, right in values if left > 0]
    return {
        "n": len(values),
        "left_total": rounded(left_total),
        "right_total": rounded(right_total),
        "weighted_savings_pct": pct_savings(left_total, right_total),
        "right_efficiency_pct": pct_efficiency(left_total, right_total),
        "mean_paired_savings_pct": mean(paired_savings),
        "mean_paired_savings_95ci": bootstrap_mean_ci(paired_savings),
    }


def compare_timing(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    field: str,
) -> dict[str, Any]:
    values = [
        (float(left[field]), float(right[field]))
        for left, right in pairs
        if is_number(left.get(field)) and is_number(right.get(field))
    ]
    left = [item[0] for item in values]
    right = [item[1] for item in values]
    deltas = [right_value - left_value for left_value, right_value in values]
    overheads = [
        (right_value - left_value) / left_value * 100
        for left_value, right_value in values
        if left_value > 0
    ]
    return {
        "n": len(values),
        "left": numeric(left),
        "right": numeric(right),
        "mean_delta_ms": mean(deltas),
        "mean_delta_95ci_ms": bootstrap_mean_ci(deltas),
        "mean_paired_overhead_pct": mean(overheads),
        "mean_paired_overhead_95ci": bootstrap_mean_ci(overheads),
    }


def hybrid_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    hybrid = [record for record in records if mode_of(record) == "hybrid"]
    valid = [record for record in hybrid if record.get("configuration", {}).get("expected_success") is True]
    safety = [record for record in hybrid if record.get("configuration", {}).get("expected_success") is False]
    return {
        "records": len(hybrid),
        "success_rate": rate(sum(bool(record.get("task_success")) for record in hybrid), len(hybrid)),
        "selected_modes": dict(sorted(Counter(str(record.get("hybrid_selected_mode")) for record in hybrid).items())),
        "valid_records": len(valid),
        "valid_selected_modes": dict(
            sorted(Counter(str(record.get("hybrid_selected_mode")) for record in valid).items())
        ),
        "safety_records": len(safety),
        "safety_selected_modes": dict(
            sorted(Counter(str(record.get("hybrid_selected_mode")) for record in safety).items())
        ),
        "mean_estimated_output_savings_pct": mean(
            [record.get("hybrid_token_savings_pct") for record in valid]
        ),
    }


def safety_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    safety = [record for record in records if verification_level(record) == "safety"]
    rollback = [record for record in safety if record.get("rollback_triggered")]
    return {
        "records": len(safety),
        "successful_records": sum(bool(record.get("task_success")) for record in safety),
        "failure_detection_rate": rate(sum(bool(record.get("failure_detected")) for record in safety), len(safety)),
        "rollback_trigger_rate": rate(len(rollback), len(safety)),
        "rollback_success_rate": rate(
            sum(record.get("rollback_success") is True for record in rollback),
            len(rollback),
        ),
        "by_language": grouped_summary(safety, lambda record: record.get("language")),
    }


def source_size_effect(triples: list[dict[str, dict[str, Any]]]) -> dict[str, Any]:
    points: list[tuple[float, float]] = []
    buckets: dict[str, list[float]] = defaultdict(list)
    for item in triples:
        control = item["control"]
        state = item["state"]
        size = control.get("source_size_bytes")
        control_tokens = control.get("estimated_output_tokens")
        state_tokens = state.get("estimated_output_tokens")
        if not (is_number(size) and is_number(control_tokens) and is_number(state_tokens)):
            continue
        if float(control_tokens) <= 0:
            continue
        savings = (float(control_tokens) - float(state_tokens)) / float(control_tokens) * 100
        points.append((float(size), savings))
        buckets[source_bucket(float(size))].append(savings)
    return {
        "n": len(points),
        "pearson_source_bytes_vs_output_savings": pearson(points),
        "positive_savings_rate": rate(sum(savings > 0 for _, savings in points), len(points)),
        "by_source_size": {
            name: {"n": len(values), "mean_savings_pct": mean(values)}
            for name, values in sorted(buckets.items())
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    comparisons = report["comparisons"]
    state = comparisons["state_vs_control"]
    aether = comparisons["aether_vs_control"]
    guarded = comparisons["aether_vs_state"]
    hybrid = comparisons["hybrid_vs_control"]
    lines = [
        "# External Repository Efficiency Report",
        "",
        f"Experiments: `{', '.join(report['experiments'])}`",
        "",
        "## Evidence",
        "",
        f"- Records: `{evidence['successful_records']}/{evidence['records']}` successful.",
        f"- Repositories: `{evidence['repository_count']}`; tasks: `{evidence['task_count']}`.",
        f"- Languages: `{', '.join(evidence['languages'])}`.",
        f"- Verification levels: `{json.dumps(evidence['verification_levels'], sort_keys=True)}`.",
        f"- Token estimators: `{', '.join(evidence['token_estimators'])}`.",
        "",
        "## Matched Efficiency",
        "",
        "| Comparison | Output-token savings | Total-token savings | Emitted-byte savings | Apply delta | Edit-to-verified delta |",
        "|---|---:|---:|---:|---:|---:|",
        comparison_row("State vs control", state),
        comparison_row("Aether vs control", aether),
        comparison_row("Aether vs state", guarded),
        comparison_row("Hybrid vs control", hybrid),
        "",
        "## Repositories",
        "",
        "| Repository | Records | Success | Mean apply ms | Mean edit-to-verified ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, item in report["by_repository"].items():
        lines.append(
            f"| {name} | {item['records']} | {pct(item['success_rate'])} | "
            f"{fmt(item['execution_time_ms']['mean'])} | {fmt(item['edit_to_verified_time_ms']['mean'])} |"
        )
    lines.extend([
        "",
        "## Hybrid",
        "",
        f"- Success rate: `{pct(report['hybrid']['success_rate'])}`.",
        f"- Valid-task selected modes: `{json.dumps(report['hybrid']['valid_selected_modes'], sort_keys=True)}`.",
        f"- Safety-task selected modes: `{json.dumps(report['hybrid']['safety_selected_modes'], sort_keys=True)}`.",
        f"- Mean estimated output savings: `{fmt(report['hybrid']['mean_estimated_output_savings_pct'])}%`.",
        "",
        "## External Rollback",
        "",
        f"- Safety records: `{report['safety']['successful_records']}/{report['safety']['records']}` successful.",
        f"- Failure detection: `{pct(report['safety']['failure_detection_rate'])}`.",
        f"- Rollback success: `{pct(report['safety']['rollback_success_rate'])}`.",
        "",
        "## Source Size",
        "",
        f"- Positive output savings: `{pct(report['source_size_effect']['positive_savings_rate'])}`.",
        f"- Pearson correlation, source bytes vs savings: `{fmt(report['source_size_effect']['pearson_source_bytes_vs_output_savings'])}`.",
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def comparison_row(label: str, item: dict[str, Any]) -> str:
    return (
        f"| {label} | {fmt(item['estimated_output_tokens']['weighted_savings_pct'])}% | "
        f"{fmt(item['estimated_total_tokens']['weighted_savings_pct'])}% | "
        f"{fmt(item['emitted_bytes']['weighted_savings_pct'])}% | "
        f"{signed(item['execution_time_ms']['mean_delta_ms'])} ms | "
        f"{signed(item['edit_to_verified_time_ms']['mean_delta_ms'])} ms |"
    )


def mode_of(record: dict[str, Any]) -> str:
    return str(record.get("configuration", {}).get("mode"))


def verification_level(record: dict[str, Any]) -> str:
    return str(record.get("configuration", {}).get("verification_level", "unspecified"))


def estimated_total_tokens(record: dict[str, Any]) -> float | None:
    input_tokens = record.get("estimated_input_tokens")
    output_tokens = record.get("estimated_output_tokens")
    if not (is_number(input_tokens) and is_number(output_tokens)):
        return None
    return float(input_tokens) + float(output_tokens)


def numeric(values: Any) -> dict[str, Any]:
    numbers = [float(value) for value in values if is_number(value)]
    if not numbers:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "stddev": None,
            "p95": None,
            "min": None,
            "max": None,
            "total": None,
        }
    ordered = sorted(numbers)
    return {
        "n": len(numbers),
        "mean": mean(numbers),
        "median": rounded(statistics.median(numbers)),
        "stddev": rounded(statistics.stdev(numbers)) if len(numbers) > 1 else 0.0,
        "p95": rounded(percentile(ordered, 0.95)),
        "min": rounded(min(numbers)),
        "max": rounded(max(numbers)),
        "total": rounded(sum(numbers)),
    }


def bootstrap_mean_ci(values: list[float], samples: int = 2000) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        value = rounded(values[0])
        return [value, value]
    rng = random.Random(0)
    means = sorted(statistics.fmean(rng.choice(values) for _ in values) for _ in range(samples))
    return [rounded(percentile(means, 0.025)), rounded(percentile(means, 0.975))]


def percentile(ordered: list[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def pearson(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in points)
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
    )
    return rounded(numerator / denominator) if denominator else None


def source_bucket(size: float) -> str:
    if size < 1024:
        return "0-1KiB"
    if size < 4096:
        return "1-4KiB"
    if size < 16384:
        return "4-16KiB"
    return "16KiB+"


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def mean(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if is_number(value)]
    return rounded(statistics.fmean(numbers)) if numbers else None


def rate(numerator: int, denominator: int) -> float | None:
    return rounded(numerator / denominator) if denominator else None


def pct_savings(left: float, right: float) -> float | None:
    return rounded((left - right) / left * 100) if left > 0 else None


def pct_efficiency(left: float, right: float) -> float | None:
    return rounded(left / right * 100) if right > 0 else None


def rounded(value: float) -> float:
    return round(value, 6)


def fmt(value: Any) -> str:
    return "n/a" if not is_number(value) else f"{float(value):.2f}"


def pct(value: Any) -> str:
    return "n/a" if not is_number(value) else f"{float(value) * 100:.2f}%"


def signed(value: Any) -> str:
    return "n/a" if not is_number(value) else f"{float(value):+.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
