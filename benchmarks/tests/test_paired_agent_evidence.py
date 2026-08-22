from __future__ import annotations

import sys
from pathlib import Path


ANALYSIS = Path(__file__).resolve().parents[1] / "analysis"
sys.path.insert(0, str(ANALYSIS))

from paired_agent_evidence import analyze, mcnemar_exact  # noqa: E402


def record(task: str, trial: int, arm: str, success: bool, tokens: int) -> dict[str, object]:
    return {
        "pair_id": f"{task}:trial-{trial}",
        "task_id": task,
        "trial": trial,
        "artifact_format": arm,
        "task_success": success,
        "format_valid": True,
        "applicable": True,
        "syntax_valid": True,
        "hidden_test_pass": success,
        "estimated_output_tokens": tokens,
        "output_bytes": tokens * 4,
        "application_time_ms": 1.0,
        "verification_time_ms": 2.0,
        "repository": "repo",
    }


def test_analyze_reports_paired_success_and_savings() -> None:
    payload = {
        "baseline_original_passes": [],
        "prompt_core_sha256": "a" * 64,
        "records": [
            record("one", 1, "aether_patch", True, 10),
            record("one", 1, "full_file", True, 100),
            record("two", 1, "aether_patch", False, 10),
            record("two", 1, "full_file", True, 100),
        ],
    }

    report = analyze(payload)

    assert report["evidence"]["pairs"] == 2
    assert report["paired_success"]["full_file_only_pass"] == 1
    assert report["paired_success"]["aether_minus_full_percentage_points"] == -50.0
    assert report["efficiency"]["aether_output_token_savings_pct"] == 90.0


def test_mcnemar_exact_handles_no_discordant_pairs() -> None:
    assert mcnemar_exact(0, 0) == 1.0
