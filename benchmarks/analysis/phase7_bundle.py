#!/usr/bin/env python
"""Create a reproducible Phase 7 public benchmark bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "benchmarks" / "results" / "public"


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    readiness = run_json(
        [
            sys.executable,
            str(Path(__file__).with_name("phase7_readiness.py")),
            "--phase4",
            str(args.phase4),
            "--phase5",
            str(args.phase5),
            "--phase6",
            str(args.phase6),
            "--state-results",
            *(str(path) for path in args.state_results),
            "--hybrid-results",
            *(str(path) for path in args.hybrid_results),
            "--external-results",
            *(str(path) for path in args.external_results),
            "--proof-results",
            *(str(path) for path in args.proof_results),
        ]
    )
    proof_score = run_json(
        [sys.executable, str(Path(__file__).with_name("proof_score.py")), *(str(path) for path in args.proof_results)]
    )
    proof_gaps = run_json(
        [sys.executable, str(Path(__file__).with_name("proof_gaps.py")), *(str(path) for path in args.proof_results)]
    )
    state = run_json(
        [sys.executable, str(Path(__file__).with_name("state_efficiency.py")), *(str(path) for path in args.state_results)]
    )
    hybrid = run_json(
        [sys.executable, str(Path(__file__).with_name("hybrid_policy.py")), *(str(path) for path in args.hybrid_results)]
    )
    token_estimates = run_json(
        [sys.executable, str(Path(__file__).with_name("token_estimates.py")), *(str(path) for path in args.token_results)]
    )
    external_efficiency = run_json(
        [
            sys.executable,
            str(Path(__file__).with_name("external_efficiency.py")),
            *(str(path) for path in args.external_results),
        ]
    )

    source_files = {
        "phase4": [args.phase4],
        "phase5": [args.phase5],
        "phase6": [args.phase6],
        "state_results": args.state_results,
        "hybrid_results": args.hybrid_results,
        "external_results": args.external_results,
        "token_results": args.token_results,
        "proof_results": args.proof_results,
    }
    bundle = {
        "bundle_version": "phase7-public-bundle-v1",
        "generated_at": now_iso(),
        "commit_sha": git_commit_sha(),
        "environment": environment_metadata(),
        "phase7_ready": readiness.get("phase7_ready"),
        "source_files": {
            group: [file_manifest(path) for path in paths]
            for group, paths in source_files.items()
        },
        "commands": reproduction_commands(args),
        "reports": {
            "phase7_readiness": readiness,
            "proof_score": proof_score,
            "proof_gaps": proof_gaps,
            "state_efficiency": state,
            "hybrid_policy": hybrid,
            "token_estimates": token_estimates,
            "external_efficiency": external_efficiency,
        },
        "limitations": limitations(),
    }

    json_path = output_dir / "phase7_public_bundle.json"
    report_path = output_dir / "PHASE7_PUBLIC_REPORT.md"
    json_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown(bundle), encoding="utf-8")

    print(f"Wrote public bundle JSON: {json_path.relative_to(REPO_ROOT)}")
    print(f"Wrote public report: {report_path.relative_to(REPO_ROOT)}")
    return 0 if bundle["phase7_ready"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Phase 7 public benchmark bundle.")
    parser.add_argument("--phase4", required=True, type=Path, help="Raw Phase 4 real-repository result.")
    parser.add_argument("--phase5", required=True, type=Path, help="Raw Phase 5 cross-language result.")
    parser.add_argument("--phase6", required=True, type=Path, help="Raw Phase 6 A/B agent result.")
    parser.add_argument("--state-results", nargs="+", required=True, type=Path)
    parser.add_argument("--hybrid-results", nargs="+", required=True, type=Path)
    parser.add_argument("--external-results", nargs="+", required=True, type=Path)
    parser.add_argument("--token-results", nargs="+", required=True, type=Path)
    parser.add_argument("--proof-results", nargs="+", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def file_manifest(path: Path) -> dict[str, Any]:
    absolute = path.resolve()
    data = absolute.read_bytes()
    return {
        "path": str(path.as_posix()),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def reproduction_commands(args: argparse.Namespace) -> list[str]:
    return [
        "python benchmarks/run.py --suite smoke --mode all-modes --trials 1 --experiment-id ci-smoke-local",
        "python benchmarks/run.py --suite all --mode hybrid --trials 1 --experiment-id hybrid-threshold-all-smoke",
        "python benchmarks/run.py --suite external-repository --mode all-modes --trials 3 --allow-network-repos --experiment-id external-matrix-allmodes-trials3-v3",
        "python benchmarks/analysis/phase_gates.py --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json",
        command_from_args("python benchmarks/analysis/phase7_readiness.py", args),
        command_from_args("python benchmarks/analysis/phase7_bundle.py", args),
    ]


def command_from_args(prefix: str, args: argparse.Namespace) -> str:
    parts = [
        prefix,
        "--phase4",
        path_arg(args.phase4),
        "--phase5",
        path_arg(args.phase5),
        "--phase6",
        path_arg(args.phase6),
        "--state-results",
        *(path_arg(path) for path in args.state_results),
        "--hybrid-results",
        *(path_arg(path) for path in args.hybrid_results),
        "--external-results",
        *(path_arg(path) for path in args.external_results),
    ]
    if "phase7_bundle.py" in prefix:
        parts.extend(["--token-results", *(path_arg(path) for path in args.token_results)])
    parts.extend(["--proof-results", *(path_arg(path) for path in args.proof_results)])
    return " ".join(parts)


def path_arg(path: Path) -> str:
    return path.as_posix()


def limitations() -> list[str]:
    return [
        "The local reproducible scope is not a universal claim about all repositories or all agents.",
        "Live token/cost telemetry is limited to small provider smoke evidence; offline token estimates are reported separately.",
        "State mode measures raw transition efficiency without validation, snapshots, or rollback.",
        "Hybrid mode is a threshold policy for product routing, not an externally validated universal optimum.",
        "External coverage is five pinned repositories and twelve tasks; it is stronger than a smoke test but still not a representative sample of all software projects.",
    ]


def render_markdown(bundle: dict[str, Any]) -> str:
    readiness = bundle["reports"]["phase7_readiness"]
    proof = bundle["reports"]["proof_score"]
    gaps = bundle["reports"]["proof_gaps"]
    state = bundle["reports"]["state_efficiency"]
    hybrid = bundle["reports"]["hybrid_policy"]
    external = readiness["external_repository"]
    tokens = bundle["reports"]["token_estimates"]
    external_efficiency = bundle["reports"]["external_efficiency"]
    external_hybrid = external_efficiency["comparisons"]["hybrid_vs_control"]
    phase = readiness["phase_gates"]
    lines = [
        "# Phase 7 Public Benchmark Report",
        "",
        f"Generated: `{bundle['generated_at']}`",
        f"Commit: `{bundle.get('commit_sha')}`",
        f"Phase 7 ready: `{str(bundle['phase7_ready']).lower()}`",
        "",
        "## Gate Summary",
        "",
        f"- Phase 4 real repositories: `{phase['phase4_real_repositories']['records']}` records, `{phase['phase4_real_repositories']['success_rate_pct']}%` success.",
        f"- Phase 5 cross-language: `{phase['phase5_cross_language']['records']}` records, `{phase['phase5_cross_language']['success_rate_pct']}%` success.",
        f"- Phase 6 A/B agent: `{phase['phase6_ab_agent']['records']}` records, `{phase['phase6_ab_agent']['success_rate_pct']}%` success.",
        f"- External pinned repositories: `{external['records']}` records across `{external['repository_count']}` repositories and `{external['task_count']}` tasks, `{external['success_rate']}` success rate.",
        f"- External verification levels: `{json.dumps(external['verification_levels'], sort_keys=True)}`.",
        f"- External rollback success: `{external['rollback_success_rate']}`.",
        f"- Tested proof scope: `{gaps['tested_scope']['passed_records']}/{gaps['tested_scope']['tested_records']}` passed.",
        f"- Conservative proof score: `{proof['overall_proof_score_pct']}%` ({proof['interpretation']}).",
        "",
        "## Efficiency",
        "",
        f"- Live output-token savings: `{proof['metrics']['output_token_savings_pct']}%` where provider telemetry exists.",
        f"- State vs Aether matched records: `{state['pairs']['state_vs_aether']['n']}`.",
        f"- State mean execution: `{state['pairs']['state_vs_aether']['left_execution_mean_ms']} ms`.",
        f"- Full Aether mean execution: `{state['pairs']['state_vs_aether']['right_execution_mean_ms']} ms`.",
        f"- Hybrid records: `{hybrid['hybrid_records']}`, success rate `{hybrid['success_rate']}`.",
        f"- Hybrid selected modes: `{json.dumps(hybrid['selected_modes'], sort_keys=True)}`.",
        f"- Offline estimated patch-vs-rewrite output savings: `{tokens['overall']['patch_vs_traditional_output_savings_pct']}%`.",
        f"- External hybrid output-token savings: `{external_hybrid['estimated_output_tokens']['weighted_savings_pct']}%`.",
        f"- External hybrid total-token savings: `{external_hybrid['estimated_total_tokens']['weighted_savings_pct']}%`.",
        f"- External hybrid emitted-byte savings: `{external_hybrid['emitted_bytes']['weighted_savings_pct']}%`.",
        f"- External hybrid edit-to-verified delta: `{external_hybrid['edit_to_verified_time_ms']['mean_delta_ms']} ms`.",
        "",
        "## Reproduction Commands",
        "",
    ]
    lines.extend(f"```bash\n{command}\n```" for command in bundle["commands"])
    lines.extend([
        "",
        "## Evidence Files",
        "",
    ])
    for group, files in bundle["source_files"].items():
        lines.append(f"- `{group}`:")
        for item in files:
            lines.append(f"  - `{item['path']}` sha256 `{item['sha256']}` bytes `{item['bytes']}`")
    lines.extend([
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in bundle["limitations"])
    lines.append("")
    return "\n".join(lines)


def git_commit_sha() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def environment_metadata() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "system": platform.system(),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
