#!/usr/bin/env python
"""Estimate dynamic method selection across full-file, state, Aether, and graph-scoped paths."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODES = ("control", "state", "aether", "hybrid")


@dataclass(frozen=True)
class Candidate:
    method: str
    source_mode: str
    success: bool
    input_tokens: int
    output_tokens: int
    edit_to_verified_ms: float
    reason: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def main() -> int:
    args = parse_args()
    records = load_records(args.raw_results)
    report = analyze(
        records,
        graph_context_savings_pct=args.graph_context_savings_pct,
        latency_token_equivalent_per_ms=args.latency_token_equivalent_per_ms,
        failure_penalty_tokens=args.failure_penalty_tokens,
        require_success=not args.allow_failed_candidates,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_results", nargs="+", type=Path)
    parser.add_argument(
        "--graph-context-savings-pct",
        type=float,
        default=0.0,
        help="Estimated input-token reduction from graph-scoped retrieval before coding.",
    )
    parser.add_argument(
        "--latency-token-equivalent-per-ms",
        type=float,
        default=0.0,
        help="Planner weight that converts one millisecond of local latency into token-equivalent cost.",
    )
    parser.add_argument(
        "--failure-penalty-tokens",
        type=float,
        default=100000.0,
        help="Penalty applied to failed candidates when --allow-failed-candidates is set.",
    )
    parser.add_argument("--allow-failed-candidates", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
    return records


def analyze(
    records: list[dict[str, Any]],
    *,
    graph_context_savings_pct: float,
    latency_token_equivalent_per_ms: float,
    failure_penalty_tokens: float,
    require_success: bool,
) -> dict[str, Any]:
    if not 0 <= graph_context_savings_pct < 100:
        raise ValueError("graph_context_savings_pct must be >= 0 and < 100")
    grouped = comparable_groups(records)
    decisions = [
        decide(
            key,
            values,
            graph_context_savings_pct=graph_context_savings_pct,
            latency_token_equivalent_per_ms=latency_token_equivalent_per_ms,
            failure_penalty_tokens=failure_penalty_tokens,
            require_success=require_success,
        )
        for key, values in sorted(grouped.items())
        if "control" in values
    ]
    decisions = [item for item in decisions if item is not None]
    baseline_tokens = sum(item["baseline_total_tokens"] for item in decisions)
    planned_tokens = sum(item["selected_total_tokens"] for item in decisions)
    baseline_latency = sum(item["baseline_edit_to_verified_ms"] for item in decisions)
    planned_latency = sum(item["selected_edit_to_verified_ms"] for item in decisions)
    return {
        "report_version": "transition-planner-v1",
        "planner": {
            "graph_context_savings_pct": graph_context_savings_pct,
            "latency_token_equivalent_per_ms": latency_token_equivalent_per_ms,
            "failure_penalty_tokens": failure_penalty_tokens,
            "require_success": require_success,
        },
        "groups": len(decisions),
        "selected_methods": dict(sorted(Counter(item["selected_method"] for item in decisions).items())),
        "token_efficiency": {
            "baseline_total_tokens": baseline_tokens,
            "planned_total_tokens": planned_tokens,
            "planned_token_savings_pct": pct_delta(baseline_tokens, planned_tokens),
        },
        "latency": {
            "baseline_edit_to_verified_ms": round(baseline_latency, 6),
            "planned_edit_to_verified_ms": round(planned_latency, 6),
            "planned_latency_savings_pct": pct_delta(baseline_latency, planned_latency),
        },
        "success": {
            "selected_success_rate": rate(sum(1 for item in decisions if item["selected_success"]), len(decisions)),
            "baseline_success_rate": rate(sum(1 for item in decisions if item["baseline_success"]), len(decisions)),
        },
        "by_repository": summarize(decisions, "repository"),
        "by_language": summarize(decisions, "language"),
        "decisions": decisions,
    }


def comparable_groups(records: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, dict[str, Any]]]:
    groups: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        mode = ((record.get("configuration") or {}).get("mode") or "").lower()
        if mode not in MODES:
            continue
        key = (str(record.get("task_id")), int((record.get("configuration") or {}).get("trial", 1)))
        groups[key][mode] = record
    return groups


def decide(
    key: tuple[str, int],
    values: dict[str, dict[str, Any]],
    *,
    graph_context_savings_pct: float,
    latency_token_equivalent_per_ms: float,
    failure_penalty_tokens: float,
    require_success: bool,
) -> dict[str, Any] | None:
    candidates = candidate_set(values, graph_context_savings_pct)
    viable = [item for item in candidates if item.success] if require_success else candidates
    if not viable:
        return None
    selected = min(
        viable,
        key=lambda item: objective(item, latency_token_equivalent_per_ms, failure_penalty_tokens),
    )
    baseline = full_file_candidate(values["control"])
    task_id, trial = key
    exemplar = values.get(selected.source_mode) or values["control"]
    return {
        "task_id": task_id,
        "trial": trial,
        "repository": exemplar.get("repository"),
        "language": exemplar.get("language"),
        "selected_method": selected.method,
        "selected_reason": selected.reason,
        "selected_success": selected.success,
        "selected_input_tokens": selected.input_tokens,
        "selected_output_tokens": selected.output_tokens,
        "selected_total_tokens": selected.total_tokens,
        "selected_edit_to_verified_ms": round(selected.edit_to_verified_ms, 6),
        "baseline_success": baseline.success,
        "baseline_total_tokens": baseline.total_tokens,
        "baseline_edit_to_verified_ms": round(baseline.edit_to_verified_ms, 6),
        "token_savings_pct": pct_delta(baseline.total_tokens, selected.total_tokens),
        "latency_savings_pct": pct_delta(baseline.edit_to_verified_ms, selected.edit_to_verified_ms),
    }


def candidate_set(values: dict[str, dict[str, Any]], graph_context_savings_pct: float) -> list[Candidate]:
    candidates = [full_file_candidate(values["control"])]
    if "state" in values:
        state = transition_candidate("state_transition", values["state"], "state")
        candidates.append(state)
        candidates.append(graph_candidate(state, graph_context_savings_pct))
    if "aether" in values:
        aether = transition_candidate("guarded_aether", values["aether"], "aether")
        candidates.append(aether)
        candidates.append(graph_candidate(aether, graph_context_savings_pct))
    if "hybrid" in values:
        selected = str(values["hybrid"].get("hybrid_selected_mode") or "hybrid")
        candidates.append(transition_candidate(f"existing_hybrid:{selected}", values["hybrid"], "hybrid"))
    return candidates


def full_file_candidate(record: dict[str, Any]) -> Candidate:
    traditional_output = int(record.get("estimated_traditional_output_tokens") or record.get("estimated_output_tokens") or 0)
    return Candidate(
        method="full_file",
        source_mode="control",
        success=bool(record.get("task_success")),
        input_tokens=int(record.get("estimated_input_tokens") or 0),
        output_tokens=traditional_output,
        edit_to_verified_ms=float(record.get("edit_to_verified_time_ms") or record.get("execution_time_ms") or 0.0),
        reason="baseline complete target-file generation",
    )


def transition_candidate(method: str, record: dict[str, Any], source_mode: str) -> Candidate:
    return Candidate(
        method=method,
        source_mode=source_mode,
        success=bool(record.get("task_success")),
        input_tokens=int(record.get("estimated_input_tokens") or 0),
        output_tokens=int(record.get("estimated_output_tokens") or 0),
        edit_to_verified_ms=float(record.get("edit_to_verified_time_ms") or record.get("execution_time_ms") or 0.0),
        reason="measured structured transition path",
    )


def graph_candidate(candidate: Candidate, graph_context_savings_pct: float) -> Candidate:
    multiplier = 1.0 - graph_context_savings_pct / 100.0
    return Candidate(
        method=f"graph_scoped_{candidate.method}",
        source_mode=candidate.source_mode,
        success=candidate.success,
        input_tokens=max(1, math.ceil(candidate.input_tokens * multiplier)),
        output_tokens=candidate.output_tokens,
        edit_to_verified_ms=candidate.edit_to_verified_ms,
        reason=f"synthetic input-context reduction of {graph_context_savings_pct}%",
    )


def objective(candidate: Candidate, latency_weight: float, failure_penalty_tokens: float) -> float:
    penalty = 0.0 if candidate.success else failure_penalty_tokens
    return candidate.total_tokens + candidate.edit_to_verified_ms * latency_weight + penalty


def summarize(decisions: list[dict[str, Any]], field: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        grouped[str(decision.get(field))].append(decision)
    return {
        key: {
            "groups": len(items),
            "selected_methods": dict(sorted(Counter(item["selected_method"] for item in items).items())),
            "mean_token_savings_pct": numeric([item["token_savings_pct"] for item in items]),
            "mean_latency_savings_pct": numeric([item["latency_savings_pct"] for item in items]),
        }
        for key, items in sorted(grouped.items())
    }


def numeric(values: list[float | None]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    if not numbers:
        return None
    return round(statistics.fmean(numbers), 6)


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def pct_delta(baseline: float, selected: float) -> float | None:
    if baseline == 0:
        return None
    return round((baseline - selected) / baseline * 100, 6)


if __name__ == "__main__":
    raise SystemExit(main())
