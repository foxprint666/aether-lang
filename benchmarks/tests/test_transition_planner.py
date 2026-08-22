from __future__ import annotations

import sys
from pathlib import Path


ANALYSIS = Path(__file__).resolve().parents[1] / "analysis"
sys.path.insert(0, str(ANALYSIS))

from transition_planner import analyze  # noqa: E402


def record(mode: str, output: int, *, success: bool = True, input_tokens: int = 1000) -> dict[str, object]:
    return {
        "task_id": "task-one",
        "repository": "repo",
        "language": "python",
        "task_success": success,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output,
        "estimated_traditional_output_tokens": 900,
        "edit_to_verified_time_ms": 10.0,
        "configuration": {"mode": mode, "trial": 1},
    }


def test_planner_keeps_full_file_when_structured_output_is_larger() -> None:
    report = analyze(
        [record("control", 900), record("state", 1200), record("aether", 1300)],
        graph_context_savings_pct=0,
        latency_token_equivalent_per_ms=0,
        failure_penalty_tokens=100000,
        require_success=True,
    )

    assert report["selected_methods"] == {"full_file": 1}
    assert report["token_efficiency"]["planned_token_savings_pct"] == 0.0


def test_planner_selects_graph_scoped_state_when_context_savings_dominate() -> None:
    report = analyze(
        [record("control", 900), record("state", 300)],
        graph_context_savings_pct=80,
        latency_token_equivalent_per_ms=0,
        failure_penalty_tokens=100000,
        require_success=True,
    )

    assert report["selected_methods"] == {"graph_scoped_state_transition": 1}
    assert report["token_efficiency"]["planned_token_savings_pct"] == 73.684211


def test_planner_filters_failed_candidates_by_default() -> None:
    report = analyze(
        [record("control", 900), record("state", 100, success=False)],
        graph_context_savings_pct=80,
        latency_token_equivalent_per_ms=0,
        failure_penalty_tokens=100000,
        require_success=True,
    )

    assert report["selected_methods"] == {"full_file": 1}
    assert report["success"]["selected_success_rate"] == 1.0
