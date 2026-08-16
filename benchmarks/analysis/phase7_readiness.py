#!/usr/bin/env python
"""Evaluate readiness for a reproducible public benchmark bundle.

Phase 7 is about public reproducibility, not just local pass rates. This gate
checks whether the benchmark has objective manifests, repeatable raw evidence,
documented reproduction commands, completed Phase 4/5/6 gates, and state-mode
efficiency evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    args = parse_args()
    phase_gates = run_json(
        [
            sys.executable,
            str(Path(__file__).with_name("phase_gates.py")),
            "--phase4",
            str(args.phase4),
            "--phase5",
            str(args.phase5),
            "--phase6",
            str(args.phase6),
        ]
    )
    state = run_json(
        [
            sys.executable,
            str(Path(__file__).with_name("state_efficiency.py")),
            *(str(path) for path in args.state_results),
        ]
    )
    hybrid = run_json(
        [
            sys.executable,
            str(Path(__file__).with_name("hybrid_policy.py")),
            *(str(path) for path in args.hybrid_results),
        ]
    )
    external = summarize_external(load_records(args.external_results))
    proof_gaps = run_json(
        [
            sys.executable,
            str(Path(__file__).with_name("proof_gaps.py")),
            *(str(path) for path in args.proof_results),
        ]
    )

    checks = {
        "phase456_done": bool(phase_gates.get("all_done")),
        "state_records_at_least_80": int(state.get("record_count") or 0) >= 80,
        "state_success_100_pct": success_is_100(state),
        "state_faster_than_aether": state_faster_than_aether(state),
        "hybrid_records_at_least_40": int(hybrid.get("hybrid_records") or 0) >= 40,
        "hybrid_success_100_pct": hybrid.get("success_rate") == 1.0,
        "hybrid_routes_to_control_state_aether": {"control", "state", "aether"} <= set(
            hybrid.get("selected_modes", {})
        ),
        "external_records_at_least_120": int(external.get("records") or 0) >= 120,
        "external_success_100_pct": external.get("success_rate") == 1.0,
        "external_repositories_at_least_5": int(external.get("repository_count") or 0) >= 5,
        "external_tasks_at_least_12": int(external.get("task_count") or 0) >= 12,
        "external_cross_language": {"python", "javascript"} <= set(external.get("languages", [])),
        "external_behavior_records_at_least_90": int(external.get("verification_levels", {}).get("behavior") or 0) >= 90,
        "external_safety_records_at_least_12": int(external.get("verification_levels", {}).get("safety") or 0) >= 12,
        "external_rollback_success_100_pct": external.get("rollback_success_rate") == 1.0,
        "external_pinned_git_manifest": external.get("pinned_git_manifest") is True,
        "tested_scope_pass_100_pct": proof_gaps.get("tested_scope", {}).get("pass_rate_pct") == 100.0,
        "benchmark_readme_documents_state_mode": file_contains(
            REPO_ROOT / "benchmarks" / "README.md",
            ["--mode state", "--mode hybrid", "--mode all-modes", "state_efficiency.py"],
        ),
        "evidence_report_documents_limits": file_contains(
            REPO_ROOT / "benchmark_evidence.md",
            ["What This Does Not Prove Yet", "State-transition fast-path"],
        ),
        "ci_runs_all_modes_smoke": file_contains(
            REPO_ROOT / ".github" / "workflows" / "benchmark-smoke.yml",
            ["--suite smoke", "--mode all-modes"],
        ),
        "task_manifests_parse": manifests_parse(REPO_ROOT / "benchmarks" / "tasks"),
    }

    report = {
        "phase7_ready": all(checks.values()),
        "checks": checks,
        "phase_gates": phase_gates,
        "state_efficiency": state,
        "hybrid_policy": hybrid,
        "external_repository": external,
        "proof_gaps": proof_gaps,
        "next_blockers": [name for name, passed in checks.items() if not passed],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["phase7_ready"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Phase 7 public benchmark readiness.")
    parser.add_argument("--phase4", required=True, type=Path, help="Raw Phase 4 real-repository result.")
    parser.add_argument("--phase5", required=True, type=Path, help="Raw Phase 5 cross-language result.")
    parser.add_argument("--phase6", required=True, type=Path, help="Raw Phase 6 A/B agent result.")
    parser.add_argument(
        "--state-results",
        nargs="+",
        required=True,
        type=Path,
        help="Raw results containing state-mode evidence.",
    )
    parser.add_argument(
        "--hybrid-results",
        nargs="+",
        required=True,
        type=Path,
        help="Raw results containing hybrid-mode evidence.",
    )
    parser.add_argument(
        "--external-results",
        nargs="+",
        required=True,
        type=Path,
        help="Raw results containing pinned external-repository evidence.",
    )
    parser.add_argument(
        "--proof-results",
        nargs="+",
        required=True,
        type=Path,
        help="Raw results used by proof_gaps.py.",
    )
    return parser.parse_args()


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload.get("records", []))
    return records


def summarize_external(records: list[dict[str, Any]]) -> dict[str, Any]:
    external = [record for record in records if record.get("category") == "external_repository"]
    manifests = {
        record.get("configuration", {}).get("repository_manifest")
        for record in external
        if record.get("configuration", {}).get("repository_manifest")
    }
    rollback = [record for record in external if record.get("rollback_triggered")]
    repositories = sorted({record.get("repository") for record in external})
    tasks = sorted({record.get("task_id") for record in external})
    verification_levels: dict[str, int] = {}
    for record in external:
        level = str(record.get("configuration", {}).get("verification_level", "unspecified"))
        verification_levels[level] = verification_levels.get(level, 0) + 1
    return {
        "records": len(external),
        "success_rate": rate(sum(1 for record in external if record.get("task_success")), len(external)),
        "repositories": repositories,
        "repository_count": len(repositories),
        "tasks": tasks,
        "task_count": len(tasks),
        "languages": sorted({record.get("language") for record in external}),
        "verification_levels": dict(sorted(verification_levels.items())),
        "rollback_success_rate": rate(
            sum(1 for record in rollback if record.get("rollback_success") is True),
            len(rollback),
        ),
        "modes": sorted({record.get("configuration", {}).get("mode") for record in external}),
        "manifests": sorted(manifests),
        "pinned_git_manifest": all(git_manifest_is_pinned(manifest) for manifest in manifests) if manifests else False,
    }


def git_manifest_is_pinned(manifest: str) -> bool:
    path = REPO_ROOT / manifest
    if not path.exists():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = payload.get("source", {})
    commit = source.get("commit")
    return (
        source.get("type") == "git"
        and isinstance(source.get("url"), str)
        and isinstance(commit, str)
        and len(commit) == 40
        and all(char in "0123456789abcdefABCDEF" for char in commit)
    )


def success_is_100(state: dict[str, Any]) -> bool:
    pairs = state.get("pairs", {})
    for item in pairs.values():
        if item.get("n", 0) > 0 and item.get("left_success_rate") != 1.0:
            return False
        if item.get("n", 0) > 0 and item.get("right_success_rate") != 1.0:
            return False
    return True


def state_faster_than_aether(state: dict[str, Any]) -> bool:
    pair = state.get("pairs", {}).get("state_vs_aether", {})
    return (
        int(pair.get("n") or 0) >= 20
        and isinstance(pair.get("right_overhead_pct"), (int, float))
        and float(pair["right_overhead_pct"]) > 0
    )


def file_contains(path: Path, needles: list[str]) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return all(needle in text for needle in needles)


def manifests_parse(path: Path) -> bool:
    try:
        for manifest in path.glob("*.json"):
            json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return False
    return True


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


if __name__ == "__main__":
    raise SystemExit(main())
