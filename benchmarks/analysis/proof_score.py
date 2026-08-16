#!/usr/bin/env python
"""Score Aether benchmark evidence maturity from one or more raw result files.

The score is intentionally conservative. It rewards repeated evaluable pairs,
provider availability, live token telemetry, safety coverage, and real-repo
coverage. It does not treat one perfect smoke run as universal proof.

Usage:
    python benchmarks/analysis/proof_score.py benchmarks/results/raw/run1.json ...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()
    records: list[dict[str, Any]] = []
    experiments: list[str] = []
    for path in args.raw_results:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
        if payload.get("experiment_id"):
            experiments.append(str(payload["experiment_id"]))
    report = build_report(records, experiments)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score benchmark evidence maturity.")
    parser.add_argument("raw_results", nargs="+", type=Path, help="Raw benchmark result JSON files.")
    return parser.parse_args()


def build_report(records: list[dict[str, Any]], experiments: list[str]) -> dict[str, Any]:
    pairs = matched_pairs(records)
    evaluable_pairs = [
        pair for pair in pairs
        if patch_was_tested(pair["control"]) and patch_was_tested(pair["aether"])
    ]
    live_pairs = [
        pair for pair in evaluable_pairs
        if has_tokens(pair["control"]) and has_tokens(pair["aether"])
    ]
    invalid = [
        record for record in records
        if (
            record.get("category") == "invalid_patch"
            or record.get("configuration", {}).get("verification_level") == "safety"
        )
        and record.get("configuration", {}).get("mode") == "aether"
        and patch_was_tested(record)
    ]
    real_repo = [
        record for record in records
        if record.get("category") in {"real_repository", "external_repository"}
        and patch_was_tested(record)
    ]
    provider_failures = [record for record in records if record.get("provider_error_type")]

    control_success = rate(sum(1 for pair in evaluable_pairs if pair["control"].get("task_success")), len(evaluable_pairs))
    aether_success = rate(sum(1 for pair in evaluable_pairs if pair["aether"].get("task_success")), len(evaluable_pairs))
    safety_detection = rate(
        sum(1 for record in invalid if record.get("failure_detected") or record.get("validation_failed")),
        len(invalid),
    )
    false_acceptance = rate(
        sum(1 for record in invalid if not record.get("failure_detected") and not record.get("validation_failed")),
        len(invalid),
    )
    total_token_savings = token_savings(live_pairs)
    output_token_savings = token_savings(live_pairs, output_only=True)
    local_time_savings = field_savings(evaluable_pairs, "execution_time_ms")
    provider_availability = rate(len(evaluable_pairs), len(pairs))

    categories = {
        "correctness": correctness_score(control_success, aether_success, len(evaluable_pairs)),
        "safety": safety_score(safety_detection, false_acceptance, len(invalid)),
        "token_efficiency": efficiency_score(total_token_savings, len(live_pairs)),
        "time_efficiency": efficiency_score(local_time_savings, len(evaluable_pairs)),
        "provider_quality": provider_score(provider_availability, len(provider_failures), len(records)),
        "real_repository_coverage": coverage_score(len(real_repo), minimum=6),
        "repeatability": coverage_score(len(evaluable_pairs), minimum=20),
    }
    overall = round(sum(categories.values()) / len(categories), 3)
    return {
        "experiments": experiments,
        "record_count": len(records),
        "matched_pairs": len(pairs),
        "evaluable_pairs": len(evaluable_pairs),
        "live_token_pairs": len(live_pairs),
        "tested_invalid_records": len(invalid),
        "real_repository_records": len(real_repo),
        "metrics": {
            "control_success_rate": control_success,
            "aether_success_rate": aether_success,
            "safety_detection_rate": safety_detection,
            "false_acceptance_rate": false_acceptance,
            "provider_availability_rate": provider_availability,
            "total_token_savings_pct": total_token_savings,
            "output_token_savings_pct": output_token_savings,
            "local_execution_savings_pct": local_time_savings,
        },
        "category_scores_pct": categories,
        "overall_proof_score_pct": overall,
        "interpretation": interpretation(overall),
    }


def matched_pairs(records: list[dict[str, Any]]) -> list[dict[str, dict[str, Any]]]:
    by_key: dict[tuple[Any, Any, Any], dict[str, dict[str, Any]]] = {}
    for record in records:
        if record.get("category") in {"invalid_patch", "rollback", "failure_injection"}:
            continue
        config = record.get("configuration") if isinstance(record.get("configuration"), dict) else {}
        mode = config.get("mode")
        if mode not in {"control", "aether"}:
            continue
        key = (record.get("experiment_id"), record.get("task_id"), config.get("trial"))
        by_key.setdefault(key, {})[mode] = record
    return [pair for pair in by_key.values() if {"control", "aether"} <= set(pair)]


def token_savings(pairs: list[dict[str, dict[str, Any]]], *, output_only: bool = False) -> float | None:
    control_total = 0.0
    aether_total = 0.0
    for pair in pairs:
        for side, total in [("control", "control_total"), ("aether", "aether_total")]:
            record = pair[side]
            value = float(record.get("output_tokens") or 0)
            if not output_only:
                value += float(record.get("input_tokens") or 0)
            if total == "control_total":
                control_total += value
            else:
                aether_total += value
    if control_total <= 0:
        return None
    return round((control_total - aether_total) / control_total * 100, 6)


def field_savings(pairs: list[dict[str, dict[str, Any]]], field: str) -> float | None:
    control_total = 0.0
    aether_total = 0.0
    count = 0
    for pair in pairs:
        control = pair["control"].get(field)
        aether = pair["aether"].get(field)
        if isinstance(control, (int, float)) and isinstance(aether, (int, float)):
            control_total += float(control)
            aether_total += float(aether)
            count += 1
    if count == 0 or control_total <= 0:
        return None
    return round((control_total - aether_total) / control_total * 100, 6)


def correctness_score(control_success: float | None, aether_success: float | None, n: int) -> float:
    if control_success is None or aether_success is None:
        return 0.0
    base = min(control_success, aether_success) * 100
    return round(base * sample_multiplier(n, target=20), 3)


def safety_score(detection: float | None, false_acceptance: float | None, n: int) -> float:
    if detection is None or false_acceptance is None:
        return 0.0
    base = max(0.0, detection - false_acceptance) * 100
    return round(base * sample_multiplier(n, target=10), 3)


def efficiency_score(savings: float | None, n: int) -> float:
    if savings is None:
        return 0.0
    base = max(0.0, min(100.0, 50.0 + savings / 2.0))
    return round(base * sample_multiplier(n, target=20), 3)


def provider_score(availability: float | None, failure_count: int, record_count: int) -> float:
    if availability is None:
        return 0.0
    penalty = min(25.0, failure_count / max(1, record_count) * 100)
    return round(max(0.0, availability * 100 - penalty), 3)


def coverage_score(count: int, *, minimum: int) -> float:
    return round(min(100.0, count / minimum * 100), 3)


def sample_multiplier(n: int, *, target: int) -> float:
    return min(1.0, n / target)


def has_tokens(record: dict[str, Any]) -> bool:
    if local_telemetry_model(record.get("model")):
        return False
    return isinstance(record.get("input_tokens"), int) and isinstance(record.get("output_tokens"), int)


def local_telemetry_model(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    return model in {
        "mock-provider",
        "replay-agent",
        "codex-subagent-simulated",
        "codex-parallel-subagents",
    }


def patch_was_tested(record: dict[str, Any]) -> bool:
    return (
        record.get("patch_size") is not None
        or record.get("validation_failed") is True
        or record.get("rollback_triggered") is True
        or record.get("execution_time_ms") is not None
    )


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def interpretation(score: float) -> str:
    if score >= 95:
        return "strong evidence for the tested scope"
    if score >= 80:
        return "good evidence, needs broader replication"
    if score >= 60:
        return "promising evidence, still limited"
    if score >= 40:
        return "early evidence only"
    return "insufficient evidence"


if __name__ == "__main__":
    raise SystemExit(main())
