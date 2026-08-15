#!/usr/bin/env python
"""Evaluate completion gates for roadmap phases 4, 5, and 6.

These gates define "done" for the current reproducible benchmark scope. They
do not claim Phase 7 public/live-provider replication.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()
    report = {
        "phase4_real_repositories": phase4(load_records(args.phase4)),
        "phase5_cross_language": phase5(load_records(args.phase5)),
        "phase6_ab_agent": phase6(load_records(args.phase6)),
    }
    report["all_done"] = all(item["done"] for item in report.values())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_done"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate benchmark phase completion gates.")
    parser.add_argument("--phase4", required=True, type=Path, help="Raw real-repository run JSON.")
    parser.add_argument("--phase5", required=True, type=Path, help="Raw cross-language/failure run JSON.")
    parser.add_argument("--phase6", required=True, type=Path, help="Raw A/B agent run JSON.")
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("records", []))


def phase4(records: list[dict[str, Any]]) -> dict[str, Any]:
    real = [record for record in records if record.get("category") == "real_repository"]
    languages = sorted({record.get("language") for record in real})
    trials = sorted({record.get("configuration", {}).get("trial") for record in real})
    checks = {
        "records_at_least_18": len(real) >= 18,
        "three_trials": len(trials) >= 3,
        "python_and_javascript": {"python", "javascript"} <= set(languages),
        "all_success": all(record.get("task_success") for record in real),
        "all_aether_mode": all(record.get("configuration", {}).get("mode") == "aether" for record in real),
    }
    return {
        "done": all(checks.values()),
        "checks": checks,
        "records": len(real),
        "languages": languages,
        "trials": trials,
        "success_rate_pct": pct(sum(1 for record in real if record.get("task_success")), len(real)),
    }


def phase5(records: list[dict[str, Any]]) -> dict[str, Any]:
    failure = [record for record in records if record.get("category") == "failure_injection"]
    by_language: dict[str, set[str]] = defaultdict(set)
    for record in failure:
        if record.get("failure_type"):
            by_language[str(record.get("language"))].add(str(record.get("failure_type")))
    required = {"syntax_error", "runtime_error", "broken_import", "timeout", "sensitive_path"}
    trials = sorted({record.get("configuration", {}).get("trial") for record in failure})
    rollback_attempts = [record for record in failure if record.get("rollback_triggered")]
    checks = {
        "records_at_least_48": len(failure) >= 48,
        "three_trials": len(trials) >= 3,
        "python_failure_classes": required <= by_language.get("python", set()),
        "javascript_failure_classes": required <= by_language.get("javascript", set()),
        "all_success": all(record.get("task_success") for record in failure),
        "all_detected": all(record.get("failure_detected") for record in failure),
        "rollback_success_when_triggered": all(record.get("rollback_success") is True for record in rollback_attempts),
    }
    return {
        "done": all(checks.values()),
        "checks": checks,
        "records": len(failure),
        "failure_classes": {language: sorted(values) for language, values in by_language.items()},
        "trials": trials,
        "success_rate_pct": pct(sum(1 for record in failure if record.get("task_success")), len(failure)),
    }


def phase6(records: list[dict[str, Any]]) -> dict[str, Any]:
    agent = [record for record in records if record.get("category") in {"agent_patch", "invalid_patch"}]
    valid = [record for record in agent if record.get("category") == "agent_patch"]
    invalid = [record for record in agent if record.get("category") == "invalid_patch"]
    trials = sorted({record.get("configuration", {}).get("trial") for record in agent})
    valid_tasks = sorted({record.get("task_id") for record in valid})
    matched_pairs = count_matched_pairs(valid)
    languages = sorted({record.get("language") for record in valid})
    checks = {
        "records_at_least_48": len(agent) >= 48,
        "three_trials": len(trials) >= 3,
        "seven_matched_programming_tasks": len(valid_tasks) >= 7,
        "matched_pairs_at_least_21": matched_pairs >= 21,
        "python_and_javascript": {"python", "javascript"} <= set(languages),
        "all_success": all(record.get("task_success") for record in agent),
        "invalid_detection_100_pct": all(record.get("failure_detected") or record.get("validation_failed") for record in invalid),
        "false_acceptance_0_pct": all(record.get("failure_detected") or record.get("validation_failed") for record in invalid),
        "command_agent_adapter": all(
            record.get("configuration", {}).get("agent_adapter") == "command_agent"
            for record in agent
        ),
    }
    return {
        "done": all(checks.values()),
        "checks": checks,
        "records": len(agent),
        "matched_pairs": matched_pairs,
        "valid_tasks": valid_tasks,
        "invalid_records": len(invalid),
        "trials": trials,
        "success_rate_pct": pct(sum(1 for record in agent if record.get("task_success")), len(agent)),
    }


def count_matched_pairs(records: list[dict[str, Any]]) -> int:
    by_key: dict[tuple[Any, Any], set[str]] = defaultdict(set)
    for record in records:
        config = record.get("configuration") if isinstance(record.get("configuration"), dict) else {}
        mode = config.get("mode")
        if mode in {"control", "aether"}:
            by_key[(record.get("task_id"), config.get("trial"))].add(mode)
    return sum(1 for modes in by_key.values() if {"control", "aether"} <= modes)


def pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 3)


if __name__ == "__main__":
    raise SystemExit(main())
