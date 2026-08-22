#!/usr/bin/env python
"""Reproducible benchmark runner for Aether.

M1 scope:
  - run deterministic correctness smoke tasks
  - record commit/environment metadata
  - emit raw JSON and processed CSV
  - support repeated trials and control/Aether modes

This runner does not call an LLM and does not fabricate token/cost metrics.
Unknown measurements are written as null.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import platform
import py_compile
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.blind_protocol import (
    BLIND_PROTOCOL_VERSION,
    build_blind_descriptor,
    descriptor_sha256 as blind_descriptor_sha256,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SDK = REPO_ROOT / "sdk" / "python"
PYTHON_SDK_VENV_SITE = PYTHON_SDK / ".venv" / "Lib" / "site-packages"
NODE_ADAPTER = REPO_ROOT / "benchmarks" / "adapters" / "node_apply_patch.js"
REPLAY_AGENT = REPO_ROOT / "benchmarks" / "agents" / "replay_agent.py"
COMMAND_AGENT = REPO_ROOT / "benchmarks" / "agents" / "command_agent.py"
DEFAULT_CONFIG = REPO_ROOT / "benchmarks" / "config" / "default.json"
CURRENT_ARGS: argparse.Namespace | None = None
HYBRID_MIN_OUTPUT_SAVINGS_PCT = 20.0
TASK_MANIFESTS = {
    "correctness": [REPO_ROOT / "benchmarks" / "tasks" / "correctness_smoke.json"],
    "failure-injection": [REPO_ROOT / "benchmarks" / "tasks" / "failure_injection.json"],
    "agent": [REPO_ROOT / "benchmarks" / "tasks" / "agent_replay.json"],
    "real-repository": [REPO_ROOT / "benchmarks" / "tasks" / "real_repository_smoke.json"],
    "external-repository": [REPO_ROOT / "benchmarks" / "tasks" / "external_repository_smoke.json"],
    "external-agent": [REPO_ROOT / "benchmarks" / "tasks" / "external_agent_unseen.json"],
    "all": [
        REPO_ROOT / "benchmarks" / "tasks" / "correctness_smoke.json",
        REPO_ROOT / "benchmarks" / "tasks" / "failure_injection.json",
        REPO_ROOT / "benchmarks" / "tasks" / "agent_replay.json",
        REPO_ROOT / "benchmarks" / "tasks" / "real_repository_smoke.json",
    ],
}
CSV_FIELDS = [
    "experiment_id",
    "timestamp",
    "commit_sha",
    "task_id",
    "repository",
    "language",
    "agent",
    "model",
    "category",
    "failure_type",
    "failure_detected",
    "patch_generated",
    "patch_size",
    "generated_patch_sha256",
    "agent_prompt_blind",
    "agent_descriptor_sha256",
    "source_size_bytes",
    "repository_size_bytes",
    "repository_file_count",
    "output_size_bytes",
    "traditional_output_size_bytes",
    "input_tokens",
    "output_tokens",
    "estimated_input_tokens",
    "estimated_output_tokens",
    "estimated_traditional_output_tokens",
    "token_estimator",
    "hybrid_selected_mode",
    "hybrid_reason",
    "hybrid_token_savings_pct",
    "tool_calls",
    "agent_attempts",
    "agent_latency_ms",
    "agent_cost_usd",
    "repository_setup_time_ms",
    "execution_time_ms",
    "validation_time_ms",
    "verification_time_ms",
    "edit_to_verified_time_ms",
    "total_task_time_ms",
    "tests_passed",
    "tests_failed",
    "syntax_error",
    "runtime_error",
    "validation_failed",
    "rollback_triggered",
    "rollback_success",
    "task_success",
    "repository_corrupted",
    "error_type",
    "provider_error_type",
    "provider_status_code",
    "provider_retryable",
    "provider_quota_exhausted",
]


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    language: str
    repository: str
    fixture: str
    source_file: str | None
    repository_manifest: str | None
    category: str
    failure_type: str | None
    description: str
    test_command: str
    verification_level: str
    timeout_ms: int
    supported_modes: list[str]
    expected_success: bool
    expected_stdout: str | None
    expected_content: list[str]
    expected_absent_content: list[str]
    expected_error_type: str | None
    expected_rollback: bool | None
    expected_failure_detected: bool | None
    patch: dict[str, Any]


def main() -> int:
    global CURRENT_ARGS
    args = parse_args()
    CURRENT_ARGS = args
    config = load_json(DEFAULT_CONFIG)
    suite = args.suite
    if suite == "smoke":
        suite = "correctness"

    if suite not in TASK_MANIFESTS:
        print(
            f"Suite '{args.suite}' is planned but not implemented in M1. "
            "Run --suite correctness or --suite failure-injection for executable suites.",
            file=sys.stderr,
        )
        return 2

    modes = ["control", "state", "aether", "hybrid"] if args.mode == "all-modes" else (
        ["control", "aether"] if args.mode == "both" else [args.mode]
    )
    experiment_id = args.experiment_id or f"bench-{utc_stamp()}-{uuid.uuid4().hex[:8]}"
    commit_sha = git_commit_sha()
    started_at = now_iso()
    tasks = load_tasks(suite)

    records: list[dict[str, Any]] = []
    for trial in range(1, args.trials + 1):
        for task in tasks:
            for mode in [mode for mode in modes if mode_supported(task, mode)]:
                records.append(run_task(task, mode, trial, experiment_id, commit_sha, args, suite))

    output = {
        "experiment_id": experiment_id,
        "benchmark_version": config["benchmark_version"],
        "started_at": started_at,
        "completed_at": now_iso(),
        "commit_sha": commit_sha,
        "suite": args.suite,
        "effective_suite": suite,
        "mode": args.mode,
        "trials": args.trials,
        "environment": environment_metadata(),
        "records": records,
        "summary": summarize(records),
    }

    raw_dir = REPO_ROOT / config["results"]["raw_dir"]
    processed_dir = REPO_ROOT / config["results"]["processed_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{experiment_id}.json"
    csv_path = processed_dir / f"{experiment_id}.csv"
    raw_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(csv_path, records)

    print(f"Wrote raw results: {raw_path.relative_to(REPO_ROOT)}")
    print(f"Wrote CSV results: {csv_path.relative_to(REPO_ROOT)}")
    print(json.dumps(output["summary"], indent=2, sort_keys=True))
    return 0 if all(r["task_success"] for r in records) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Aether benchmarks.")
    parser.add_argument(
        "--suite",
        default="correctness",
        choices=[
            "smoke",
            "correctness",
            "failure-injection",
            "agent",
            "real-repository",
            "external-repository",
            "external-agent",
            "all",
        ],
        help="Benchmark suite to run.",
    )
    parser.add_argument(
        "--mode",
        default="both",
        choices=["control", "state", "aether", "hybrid", "both", "all-modes"],
        help="Execution mode.",
    )
    parser.add_argument("--trials", type=int, default=1, help="Repeated trials per task/mode.")
    parser.add_argument("--experiment-id", help="Stable experiment id for reproducible output names.")
    parser.add_argument(
        "--agent-adapter",
        default="replay",
        choices=["replay", "command"],
        help="Agent adapter used by the agent suite.",
    )
    parser.add_argument(
        "--agent-command",
        nargs=argparse.REMAINDER,
        help="Command used by --agent-adapter command. It receives the task descriptor on stdin.",
    )
    parser.add_argument("--agent-retries", type=int, default=0, help="Retries for command-backed agents.")
    parser.add_argument("--agent-timeout-ms", type=int, default=120000, help="Per-attempt agent timeout.")
    parser.add_argument(
        "--hybrid-min-output-savings-pct",
        type=float,
        default=HYBRID_MIN_OUTPUT_SAVINGS_PCT,
        help="Minimum estimated output-token savings required for hybrid mode to choose state.",
    )
    parser.add_argument(
        "--allow-network-repos",
        action="store_true",
        help="Allow real-repository manifests to clone external git repositories.",
    )
    parser.add_argument(
        "--keep-workdirs",
        action="store_true",
        help="Keep temporary task work directories for debugging.",
    )
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be >= 1")
    if args.agent_retries < 0:
        parser.error("--agent-retries must be >= 0")
    if args.hybrid_min_output_savings_pct < 0:
        parser.error("--hybrid-min-output-savings-pct must be >= 0")
    if args.agent_adapter == "command" and not args.agent_command:
        parser.error("--agent-adapter command requires --agent-command ...")
    if args.suite == "external-agent" and args.agent_adapter != "command":
        parser.error("--suite external-agent requires --agent-adapter command")
    return args


def load_tasks(suite: str) -> list[BenchmarkTask]:
    tasks: list[BenchmarkTask] = []
    for manifest_path in TASK_MANIFESTS[suite]:
        manifest = load_json(manifest_path)
        tasks.extend(
            BenchmarkTask(
            task_id=item["task_id"],
            language=item["language"],
            repository=item["repository"],
            fixture=item["fixture"],
            source_file=item.get("source_file"),
            repository_manifest=item.get("repository_manifest"),
            category=item.get("category", "uncategorized"),
            failure_type=item.get("failure_type"),
            description=item["description"],
            test_command=item["test_command"],
            verification_level=str(item.get("verification_level", "syntax")),
            timeout_ms=int(item["timeout_ms"]),
            supported_modes=list(item["supported_modes"]),
            expected_success=bool(item["expected_success"]),
            expected_stdout=item.get("expected_stdout"),
            expected_content=list(item.get("expected_content", [])),
            expected_absent_content=list(item.get("expected_absent_content", [])),
            expected_error_type=item.get("expected_error_type"),
            expected_rollback=item.get("expected_rollback"),
            expected_failure_detected=item.get("expected_failure_detected"),
            patch=dict(item.get("patch", {})),
        )
            for item in manifest["tasks"]
        )
    return tasks


def mode_supported(task: BenchmarkTask, mode: str) -> bool:
    if mode == "hybrid":
        return bool({"control", "state", "aether"} & set(task.supported_modes))
    return mode in task.supported_modes


def run_task(
    task: BenchmarkTask,
    mode: str,
    trial: int,
    experiment_id: str,
    commit_sha: str | None,
    args: argparse.Namespace,
    suite: str,
) -> dict[str, Any]:
    task_started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix=f"aether-bench-{task.task_id}-{mode}-"))
    before_hash = ""
    after_hash = ""
    error_type: str | None = None
    error_detail: str | None = None
    validation_time_ms: float | None = None
    repository_setup_time_ms: float | None = None
    execution_time_ms: float | None = None
    verification_time_ms: float | None = None
    edit_to_verified_time_ms: float | None = None
    edit_started: float | None = None
    tests_passed = 0
    tests_failed = 0
    syntax_error = False
    runtime_error = False
    validation_failed = False
    rollback_triggered = False
    rollback_success: bool | None = None
    patch_generated = mode in {"state", "aether"} or is_agent_task(task)
    patch_size: int | None = None
    generated_patch_sha256: str | None = None
    source_size_bytes: int | None = None
    repository_size_bytes: int | None = None
    repository_file_count: int | None = None
    output_size_bytes: int | None = None
    traditional_output_size_bytes: int | None = None
    usage: dict[str, Any] = {}
    agent_info: dict[str, Any] = {}
    hybrid_decision: dict[str, Any] = {}
    task_success = False
    failure_detected = False

    try:
        setup_started = time.perf_counter()
        write_project(workdir, task)
        repository_setup_time_ms = elapsed_ms(setup_started)
        before_hash = tree_hash(workdir)
        source_path = workdir / source_file_for(task)
        source_size_bytes = len(source_path.read_bytes())
        repository_size_bytes, repository_file_count = tree_size(workdir)
        if mode == "control":
            if is_agent_task(task):
                patch, agent_info, usage = generate_patch(workdir, task, args, trial)
                patch_size = len(json.dumps(patch, sort_keys=True).encode("utf-8"))
                generated_patch_sha256 = patch_sha256(patch)
                reference_output = reference_output_for_metrics(workdir, task, patch)
                usage.update(estimate_patch_tokens(workdir, task, patch, trial, args, reference_output))
                output_size_bytes = patch_size
                traditional_output_size_bytes = encoded_size(reference_output)
                t0 = time.perf_counter()
                edit_started = t0
                apply_unchecked_patch(workdir, patch)
            elif uses_reference_rewrite_control(task):
                patch = build_patch(task)
                reference_output = render_reference_output(workdir, task, patch)
                usage.update(estimate_full_rewrite_tokens(workdir, task, reference_output, trial, args))
                patch_generated = True
                patch_size = len(reference_output.encode("utf-8"))
                output_size_bytes = patch_size
                traditional_output_size_bytes = patch_size
                t0 = time.perf_counter()
                edit_started = t0
                apply_full_rewrite(workdir, task, reference_output)
            else:
                t0 = time.perf_counter()
                edit_started = t0
                apply_control_edit(workdir, task)
            execution_time_ms = elapsed_ms(t0)
        elif mode == "state":
            patch, agent_info, usage = generate_patch(workdir, task, args, trial)
            patch_size = len(json.dumps(patch, sort_keys=True).encode("utf-8"))
            generated_patch_sha256 = patch_sha256(patch)
            reference_output = reference_output_for_metrics(workdir, task, patch)
            usage.update(estimate_patch_tokens(workdir, task, patch, trial, args, reference_output))
            output_size_bytes = patch_size
            traditional_output_size_bytes = encoded_size(reference_output)
            t0 = time.perf_counter()
            edit_started = t0
            apply_state_patch(workdir, patch)
            execution_time_ms = elapsed_ms(t0)
        elif mode == "aether":
            patch, agent_info, usage = generate_patch(workdir, task, args, trial)
            patch_size = len(json.dumps(patch, sort_keys=True).encode("utf-8"))
            generated_patch_sha256 = patch_sha256(patch)
            reference_output = reference_output_for_metrics(workdir, task, patch)
            usage.update(estimate_patch_tokens(workdir, task, patch, trial, args, reference_output))
            output_size_bytes = patch_size
            traditional_output_size_bytes = encoded_size(reference_output)
            t0 = time.perf_counter()
            edit_started = t0
            result = apply_aether_patch(workdir, patch)
            execution_time_ms = elapsed_ms(t0)
            validation_time_ms = result["validation_time_ms"]
            validation_failed = result["validation_failed"]
            rollback_triggered = result["rolled_back"]
            rollback_success = True if rollback_triggered and tree_hash(workdir) == before_hash else None
            if not result["ok"]:
                error_type = "validation_failed" if validation_failed else "aether_apply_failed"
                error_detail = "; ".join(stringify_error(item) for item in result["errors"])
        else:
            patch, agent_info, usage = generate_patch(workdir, task, args, trial)
            patch_size = len(json.dumps(patch, sort_keys=True).encode("utf-8"))
            generated_patch_sha256 = patch_sha256(patch)
            reference_output = reference_output_for_metrics(workdir, task, patch)
            usage.update(estimate_patch_tokens(workdir, task, patch, trial, args, reference_output))
            output_size_bytes = patch_size
            traditional_output_size_bytes = encoded_size(reference_output)
            hybrid_decision = choose_hybrid_mode(task, usage, args)
            selected_mode = hybrid_decision["selected_mode"]
            if selected_mode == "control":
                if reference_output is not None:
                    usage.update(estimate_full_rewrite_tokens(workdir, task, reference_output, trial, args))
                    output_size_bytes = encoded_size(reference_output)
                t0 = time.perf_counter()
                edit_started = t0
                if (
                    reference_output is not None
                    and (uses_reference_rewrite_control(task) or is_blind_agent_task(task))
                ):
                    apply_full_rewrite(workdir, task, reference_output)
                elif is_agent_task(task):
                    apply_unchecked_patch(workdir, patch)
                else:
                    apply_control_edit(workdir, task)
                execution_time_ms = elapsed_ms(t0)
            elif selected_mode == "state":
                t0 = time.perf_counter()
                edit_started = t0
                apply_state_patch(workdir, patch)
                execution_time_ms = elapsed_ms(t0)
            else:
                t0 = time.perf_counter()
                edit_started = t0
                result = apply_aether_patch(workdir, patch)
                execution_time_ms = elapsed_ms(t0)
                validation_time_ms = result["validation_time_ms"]
                validation_failed = result["validation_failed"]
                rollback_triggered = result["rolled_back"]
                rollback_success = True if rollback_triggered and tree_hash(workdir) == before_hash else None
                if not result["ok"]:
                    error_type = "validation_failed" if validation_failed else "aether_apply_failed"
                    error_detail = "; ".join(stringify_error(item) for item in result["errors"])

        verification_started = time.perf_counter()
        source_path = workdir / source_file_for(task)
        compile_result = check_syntax(source_path, task.language)
        if not compile_result[0]:
            syntax_error = True
            error_type = error_type or "syntax_error"
            error_detail = error_detail or compile_result[1]

        run_result = run_verification(workdir, source_path, task)
        if run_result.returncode == 0:
            tests_passed += 1
        else:
            tests_failed += 1
            runtime_error = True
            error_type = error_type or "runtime_error"
            error_detail = error_detail or run_result.stderr.strip()
        failure_detected = bool(error_type or syntax_error or runtime_error or validation_failed)

        content = source_path.read_text(encoding="utf-8")
        observed_success = (
            not syntax_error
            and not runtime_error
            and not validation_failed
            and output_matches(task, run_result.stdout)
            and content_matches(task, content)
        )
        if task.expected_success:
            task_success = observed_success
        else:
            expected_error_matches = (
                task.expected_error_type is None or task.expected_error_type == error_type
            )
            expected_rollback_matches = (
                task.expected_rollback is None or task.expected_rollback == rollback_triggered
            )
            expected_failure_matches = (
                task.expected_failure_detected is None
                or task.expected_failure_detected == failure_detected
            )
            repository_restored = tree_hash(workdir) == before_hash
            task_success = (
                expected_error_matches
                and expected_rollback_matches
                and expected_failure_matches
                and (not rollback_triggered or repository_restored)
            )
        verification_time_ms = elapsed_ms(verification_started)
        if edit_started is not None:
            edit_to_verified_time_ms = elapsed_ms(edit_started)
    except Exception as exc:  # Keep benchmark failures visible in raw output.
        error_type = exc.__class__.__name__
        error_detail = f"{exc}\n{traceback.format_exc(limit=8)}"
        tests_failed += 1
    finally:
        after_hash = tree_hash(workdir) if workdir.exists() else ""
        repository_corrupted = (
            failure_detected
            and after_hash != before_hash
            and rollback_success is not True
        )
        provider_error = classify_provider_error(error_detail)
        if not args.keep_workdirs:
            shutil.rmtree(workdir, ignore_errors=True)

    return {
        "experiment_id": experiment_id,
        "timestamp": now_iso(),
        "commit_sha": commit_sha,
        "task_id": task.task_id,
        "repository": task.repository,
        "language": task.language,
        "agent": agent_name(mode, task, args.agent_adapter),
        "model": usage.get("model") or ("replay-agent" if is_agent_task(task) else None),
        "category": task.category,
        "failure_type": task.failure_type,
        "failure_detected": failure_detected,
        "configuration": {
            "mode": mode,
            "trial": trial,
            "suite": suite,
            "workdir_kept": bool(args.keep_workdirs),
            "test_command": task.test_command,
            "verification_level": task.verification_level,
            "timeout_ms": task.timeout_ms,
            "expected_success": task.expected_success,
            "category": task.category,
            "failure_type": task.failure_type,
            "agent_generation_excluded_from_execution_time": True,
            "repository_manifest": task.repository_manifest,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "error_detail": error_detail,
            "agent_adapter": agent_adapter_name(task, args.agent_adapter),
            "agent_prompt_blind": is_blind_agent_task(task),
            "agent_descriptor_sha256": agent_info.get("descriptor_sha256"),
            "blind_protocol": agent_info.get("blind_protocol"),
            "oracle_used_during_generation": False if is_blind_agent_task(task) else None,
            "agent": agent_info or None,
            "hybrid_selected_mode": hybrid_decision.get("selected_mode"),
            "hybrid_reason": hybrid_decision.get("reason"),
            "hybrid_min_output_savings_pct": args.hybrid_min_output_savings_pct if mode == "hybrid" else None,
        },
        "patch_generated": patch_generated,
        "patch_size": patch_size,
        "generated_patch_sha256": generated_patch_sha256,
        "agent_prompt_blind": is_blind_agent_task(task),
        "agent_descriptor_sha256": agent_info.get("descriptor_sha256"),
        "source_size_bytes": source_size_bytes,
        "repository_size_bytes": repository_size_bytes,
        "repository_file_count": repository_file_count,
        "output_size_bytes": output_size_bytes,
        "traditional_output_size_bytes": traditional_output_size_bytes,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "estimated_input_tokens": usage.get("estimated_input_tokens"),
        "estimated_output_tokens": usage.get("estimated_output_tokens"),
        "estimated_traditional_output_tokens": usage.get("estimated_traditional_output_tokens"),
        "token_estimator": usage.get("token_estimator"),
        "hybrid_selected_mode": hybrid_decision.get("selected_mode"),
        "hybrid_reason": hybrid_decision.get("reason"),
        "hybrid_token_savings_pct": hybrid_decision.get("token_savings_pct"),
        "tool_calls": usage.get("tool_calls"),
        "agent_attempts": usage.get("agent_attempts"),
        "agent_latency_ms": usage.get("agent_latency_ms"),
        "agent_cost_usd": usage.get("agent_cost_usd"),
        "repository_setup_time_ms": rounded_ms(repository_setup_time_ms),
        "execution_time_ms": round(execution_time_ms, 3) if execution_time_ms is not None else None,
        "validation_time_ms": round(validation_time_ms, 3) if validation_time_ms is not None else None,
        "verification_time_ms": rounded_ms(verification_time_ms),
        "edit_to_verified_time_ms": rounded_ms(edit_to_verified_time_ms),
        "total_task_time_ms": rounded_ms(elapsed_ms(task_started)),
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "syntax_error": syntax_error,
        "runtime_error": runtime_error,
        "validation_failed": validation_failed,
        "rollback_triggered": rollback_triggered,
        "rollback_success": rollback_success,
        "task_success": task_success,
        "repository_corrupted": repository_corrupted,
        "error_type": error_type,
        "provider_error_type": provider_error["provider_error_type"],
        "provider_status_code": provider_error["provider_status_code"],
        "provider_retryable": provider_error["provider_retryable"],
        "provider_quota_exhausted": provider_error["provider_quota_exhausted"],
    }


def apply_aether_patch(workdir: Path, patch: dict[str, Any]) -> Any:
    target_file = str(patch.get("target", {}).get("file", ""))
    if target_file.endswith(".js"):
        return apply_node_patch(workdir, patch)
    return apply_python_patch(workdir, patch)


def apply_state_patch(workdir: Path, patch: dict[str, Any]) -> None:
    target_file = str(patch.get("target", {}).get("file", ""))
    if target_file.endswith(".js"):
        result = apply_node_patch(workdir, patch, unchecked=True)
        if not result["ok"]:
            raise RuntimeError("; ".join(stringify_error(item) for item in result["errors"]))
        return
    apply_python_state_patch(workdir, patch)


def apply_unchecked_patch(workdir: Path, patch: dict[str, Any]) -> None:
    target_file = str(patch.get("target", {}).get("file", ""))
    if target_file.endswith(".js"):
        result = apply_node_patch(workdir, patch, unchecked=True)
        if not result["ok"]:
            raise RuntimeError("; ".join(stringify_error(item) for item in result["errors"]))
        return
    apply_python_unchecked_patch(workdir, patch)


def uses_reference_rewrite_control(task: BenchmarkTask) -> bool:
    return task.category == "external_repository"


def reference_output_for_metrics(
    workdir: Path,
    task: BenchmarkTask,
    patch: dict[str, Any],
) -> str | None:
    if task.category not in {"external_repository", "external_agent_patch"} or not task.expected_success:
        return None
    return render_reference_output(workdir, task, patch)


def render_reference_output(
    workdir: Path,
    task: BenchmarkTask,
    patch: dict[str, Any],
) -> str:
    reference_root = Path(tempfile.mkdtemp(prefix="aether-bench-reference-"))
    try:
        relative_path = Path(source_file_for(task))
        source = workdir / relative_path
        target = reference_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        apply_state_patch(reference_root, patch)
        return target.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(reference_root, ignore_errors=True)


def apply_full_rewrite(workdir: Path, task: BenchmarkTask, source: str) -> None:
    target = workdir / source_file_for(task)
    target.write_bytes(source.encode("utf-8"))


def encoded_size(value: str | None) -> int | None:
    return len(value.encode("utf-8")) if value is not None else None


def patch_sha256(patch: dict[str, Any]) -> str:
    payload = json.dumps(patch, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def apply_python_patch(workdir: Path, patch: dict[str, Any]) -> dict[str, Any]:
    ensure_python_sdk_paths()
    from ai_runtime.orchestrator import PatchOrchestrator

    orchestrator = PatchOrchestrator(project_root=workdir, ae_binary=None)
    result = orchestrator.apply(patch)
    return {
        "ok": result.ok,
        "validation_failed": not result.ok and not result.rolled_back,
        "validation_time_ms": result.validation.elapsed_ms if result.validation else None,
        "rolled_back": result.rolled_back,
        "errors": result.errors,
    }


def apply_python_state_patch(workdir: Path, patch: dict[str, Any]) -> None:
    ensure_python_sdk_paths()
    from ai_runtime.ast.engine import apply_patch

    apply_patch(patch, str(workdir))


def apply_python_unchecked_patch(workdir: Path, patch: dict[str, Any]) -> None:
    ensure_python_sdk_paths()
    from ai_runtime.ast.engine import apply_patch

    apply_patch(patch, str(workdir))


def ensure_python_sdk_paths() -> None:
    if str(PYTHON_SDK) not in sys.path:
        sys.path.insert(0, str(PYTHON_SDK))
    missing = [name for name in ("jsonschema", "libcst", "pathspec") if importlib.util.find_spec(name) is None]
    if not missing:
        return
    if python_sdk_venv_is_compatible() and str(PYTHON_SDK_VENV_SITE) not in sys.path:
        sys.path.insert(1, str(PYTHON_SDK_VENV_SITE))
        return
    raise RuntimeError(
        "Python benchmark dependencies are missing or use an incompatible interpreter: "
        f"{', '.join(missing)}. Run the benchmark from a Python {sys.version_info.major}."
        f"{sys.version_info.minor} environment with sdk/python installed."
    )


def python_sdk_venv_is_compatible() -> bool:
    if not PYTHON_SDK_VENV_SITE.exists():
        return False
    config_path = PYTHON_SDK / ".venv" / "pyvenv.cfg"
    if not config_path.exists():
        return True
    match = re.search(
        r"(?m)^version(?:_info)?\s*=\s*(\d+)\.(\d+)",
        config_path.read_text(encoding="utf-8"),
    )
    if match is None:
        return True
    return (int(match.group(1)), int(match.group(2))) == sys.version_info[:2]


def apply_node_patch(workdir: Path, patch: dict[str, Any], unchecked: bool = False) -> dict[str, Any]:
    patch_path = workdir / ".benchmark-patch.json"
    patch_path.write_text(json.dumps(patch), encoding="utf-8")
    result = subprocess.run(
        ["node", str(NODE_ADAPTER), str(workdir), str(patch_path), "--unchecked" if unchecked else "--aether"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "validation_failed": False,
            "validation_time_ms": None,
            "rolled_back": False,
            "errors": [result.stderr.strip() or result.stdout.strip()],
        }
    if result.returncode != 0 and payload.get("ok"):
        payload["ok"] = False
        payload.setdefault("errors", []).append(result.stderr.strip())
    return {
        "ok": bool(payload.get("ok")),
        "validation_failed": bool(payload.get("validation_failed")),
        "validation_time_ms": payload.get("validation_time_ms"),
        "rolled_back": bool(payload.get("rolled_back")),
        "errors": payload.get("errors") or [],
    }


def build_patch(task: BenchmarkTask) -> dict[str, Any]:
    patch = {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": task.patch["action"],
        "target": dict(task.patch["target"]),
        "changes": dict(task.patch["changes"]),
        "metadata": {
            "agent_id": "benchmark-smoke",
            "model": "none",
            "intent": task.description[:500],
            "created_at": now_iso(),
        },
    }
    return patch


def generate_patch(
    workdir: Path,
    task: BenchmarkTask,
    args: argparse.Namespace,
    trial: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not is_agent_task(task):
        return build_patch(task), {}, {}

    descriptor_path = workdir / ".agent-task.json"
    descriptor = build_agent_descriptor(workdir, task, trial=trial, include_reference_patch=args.agent_adapter == "replay")
    descriptor_path.write_text(json.dumps(descriptor, indent=2, sort_keys=True), encoding="utf-8")
    if args.agent_adapter == "command":
        patch, agent_info, usage = run_command_agent(descriptor_path, args)
    else:
        patch, agent_info, usage = run_replay_agent(descriptor_path)
    agent_info = dict(agent_info)
    agent_info["descriptor_sha256"] = descriptor_hash(descriptor)
    agent_info["blind"] = is_blind_agent_task(task)
    agent_info["blind_protocol"] = BLIND_PROTOCOL_VERSION if is_blind_agent_task(task) else None
    return patch, agent_info, usage


def estimate_patch_tokens(
    workdir: Path,
    task: BenchmarkTask,
    patch: dict[str, Any],
    trial: int,
    args: argparse.Namespace,
    traditional_output: str | None = None,
) -> dict[str, Any]:
    estimator = token_estimator_name()
    prompt_payload = build_token_prompt_payload(workdir, task, trial, args)
    patch_output = json.dumps(patch, sort_keys=True)
    source_path = workdir / source_file_for(task)
    if traditional_output is None:
        traditional_output = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    return {
        "estimated_input_tokens": count_tokens(json.dumps(prompt_payload, sort_keys=True)),
        "estimated_output_tokens": count_tokens(patch_output),
        "estimated_traditional_output_tokens": count_tokens(traditional_output),
        "token_estimator": estimator,
    }


def estimate_full_rewrite_tokens(
    workdir: Path,
    task: BenchmarkTask,
    rewritten_source: str,
    trial: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    prompt_payload = build_full_rewrite_prompt_payload(workdir, task, trial, args)
    output_tokens = count_tokens(rewritten_source)
    return {
        "estimated_input_tokens": count_tokens(json.dumps(prompt_payload, sort_keys=True)),
        "estimated_output_tokens": output_tokens,
        "estimated_traditional_output_tokens": output_tokens,
        "token_estimator": token_estimator_name(),
    }


def choose_hybrid_mode(
    task: BenchmarkTask,
    usage: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    savings = estimated_output_savings_pct(usage)
    supported = set(task.supported_modes)
    threshold = float(args.hybrid_min_output_savings_pct)

    if not task.expected_success and "aether" in supported:
        return {
            "selected_mode": "aether",
            "reason": "safety_task_uses_guarded_aether",
            "token_savings_pct": savings,
        }

    if (
        savings is not None
        and savings >= threshold
        and "state" in supported
    ):
        return {
            "selected_mode": "state",
            "reason": "structured_patch_meets_token_savings_threshold",
            "token_savings_pct": savings,
        }

    if "control" in supported:
        return {
            "selected_mode": "control",
            "reason": "full_rewrite_or_direct_edit_below_token_savings_threshold",
            "token_savings_pct": savings,
        }

    if "state" in supported:
        return {
            "selected_mode": "state",
            "reason": "state_available_without_control_baseline",
            "token_savings_pct": savings,
        }

    return {
        "selected_mode": "aether",
        "reason": "fallback_to_guarded_aether",
        "token_savings_pct": savings,
    }


def estimated_output_savings_pct(usage: dict[str, Any]) -> float | None:
    patch_tokens = usage.get("estimated_output_tokens")
    traditional_tokens = usage.get("estimated_traditional_output_tokens")
    if (
        not isinstance(patch_tokens, int)
        or not isinstance(traditional_tokens, int)
        or traditional_tokens <= 0
    ):
        return None
    return round((traditional_tokens - patch_tokens) / traditional_tokens * 100, 6)


def build_token_prompt_payload(
    workdir: Path,
    task: BenchmarkTask,
    trial: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if is_agent_task(task):
        return build_agent_descriptor(workdir, task, trial=trial, include_reference_patch=False)
    source_path = workdir / source_file_for(task)
    return {
        "task_id": task.task_id,
        "trial": trial,
        "language": task.language,
        "repository": task.repository,
        "category": task.category,
        "description": task.description,
        "source_file": source_file_for(task),
        "source": source_path.read_text(encoding="utf-8") if source_path.exists() else "",
        "patch_schema": {
            "required": ["schema_version", "patch_id", "action", "target", "changes", "metadata"],
            "schema_version": "1.0",
            "target_file": source_file_for(task),
        },
        "agent_adapter": args.agent_adapter if is_agent_task(task) else None,
    }


def build_full_rewrite_prompt_payload(
    workdir: Path,
    task: BenchmarkTask,
    trial: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    source_path = workdir / source_file_for(task)
    return {
        "task_id": task.task_id,
        "trial": trial,
        "language": task.language,
        "repository": task.repository,
        "category": task.category,
        "description": task.description,
        "source_file": source_file_for(task),
        "source": source_path.read_text(encoding="utf-8") if source_path.exists() else "",
        "output_contract": "Return the complete updated source file.",
        "agent_adapter": args.agent_adapter if is_agent_task(task) else None,
    }


def token_estimator_name() -> str:
    return "tiktoken:cl100k_base" if tiktoken_encoding() is not None else "heuristic:regex-v1"


def count_tokens(text: str) -> int:
    encoding = tiktoken_encoding()
    if encoding is not None:
        return len(encoding.encode(text))
    return len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))


def tiktoken_encoding() -> Any:
    if not hasattr(tiktoken_encoding, "_encoding"):
        encoding = None
        try:
            import tiktoken  # type: ignore[import-not-found]

            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            encoding = None
        setattr(tiktoken_encoding, "_encoding", encoding)
    return getattr(tiktoken_encoding, "_encoding")


def build_agent_descriptor(
    workdir: Path,
    task: BenchmarkTask,
    *,
    trial: int,
    include_reference_patch: bool,
) -> dict[str, Any]:
    source_path = workdir / source_file_for(task)
    if is_blind_agent_task(task):
        return build_blind_descriptor(
            task=task.task_id,
            trial=trial,
            language=task.language,
            repository=task.repository,
            fixture=task.fixture,
            source_file=source_file_for(task),
            description=task.description,
            source=source_path.read_text(encoding="utf-8") if source_path.exists() else "",
        )
    descriptor: dict[str, Any] = {
        "task_id": task.task_id,
        "trial": trial,
        "language": task.language,
        "repository": task.repository,
        "fixture": task.fixture,
        "category": task.category,
        "failure_type": task.failure_type,
        "source_file": source_file_for(task),
        "description": task.description,
        "test_command": task.test_command,
        "timeout_ms": task.timeout_ms,
        "source": source_path.read_text(encoding="utf-8") if source_path.exists() else "",
        "acceptance": {
            "expected_stdout": task.expected_stdout,
            "expected_content": task.expected_content,
            "expected_absent_content": task.expected_absent_content,
            "expected_success": task.expected_success,
            "expected_error_type": task.expected_error_type,
            "expected_rollback": task.expected_rollback,
            "expected_failure_detected": task.expected_failure_detected,
        },
        "patch_schema": {
            "required": ["schema_version", "patch_id", "action", "target", "changes", "metadata"],
            "schema_version": "1.0",
            "target_file": source_file_for(task),
        },
    }
    if include_reference_patch:
        descriptor["patch"] = task.patch
    return descriptor


def descriptor_hash(descriptor: dict[str, Any]) -> str:
    if descriptor.get("protocol_version") == BLIND_PROTOCOL_VERSION:
        return blind_descriptor_sha256(descriptor)
    payload = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_replay_agent(descriptor_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, str(REPLAY_AGENT), str(descriptor_path)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    try:
        patch = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Replay agent emitted invalid JSON: {exc}") from exc
    if not isinstance(patch, dict) or "patch_id" not in patch:
        raise RuntimeError("Replay agent did not emit a patch object")
    return patch, {"adapter": "replay_agent", "attempt_count": 1}, {
        "agent_attempts": 1,
        "agent_latency_ms": round(elapsed_ms(t0), 3),
    }


def run_command_agent(
    descriptor_path: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    command = [
        sys.executable,
        str(COMMAND_AGENT),
        str(descriptor_path),
        "--retries",
        str(args.agent_retries),
        "--timeout-ms",
        str(args.agent_timeout_ms),
        "--command",
        *(args.agent_command or []),
    ]
    result = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        timeout=(args.agent_retries + 1) * (args.agent_timeout_ms / 1000 + 5),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Command agent emitted invalid JSON: {exc}") from exc
    patch = envelope.get("patch")
    if not isinstance(patch, dict) or "patch_id" not in patch:
        raise RuntimeError("Command agent did not emit a patch object")
    agent_info = envelope.get("agent") if isinstance(envelope.get("agent"), dict) else {}
    raw_usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    usage = {
        "input_tokens": raw_usage.get("input_tokens"),
        "output_tokens": raw_usage.get("output_tokens"),
        "tool_calls": raw_usage.get("tool_calls"),
        "agent_attempts": agent_info.get("attempt_count"),
        "agent_latency_ms": agent_info.get("elapsed_ms") or raw_usage.get("latency_ms"),
        "agent_cost_usd": raw_usage.get("cost_usd"),
        "model": raw_usage.get("model"),
    }
    return patch, agent_info, usage


def agent_name(mode: str, task: BenchmarkTask, adapter: str) -> str:
    if mode == "control":
        return "none" if not is_agent_task(task) else f"{adapter}-agent"
    if is_agent_task(task):
        return f"{adapter}-agent"
    return "aether-orchestrator"


def agent_adapter_name(task: BenchmarkTask, adapter: str) -> str | None:
    if not is_agent_task(task):
        return None
    return "replay_agent" if adapter == "replay" else "command_agent"


def is_agent_task(task: BenchmarkTask) -> bool:
    return task.category in {"agent_patch", "external_agent_patch"} or task.task_id.startswith("agent-")


def is_blind_agent_task(task: BenchmarkTask) -> bool:
    return task.category == "external_agent_patch"


def write_project(workdir: Path, task: BenchmarkTask) -> None:
    if task.fixture.startswith("repo:"):
        write_repository_fixture(workdir, task)
    elif task.language == "python":
        write_python_project(workdir, task.fixture)
    elif task.language == "javascript":
        write_javascript_project(workdir, task.fixture)
    else:
        raise ValueError(f"Unsupported benchmark language: {task.language}")


def write_repository_fixture(workdir: Path, task: BenchmarkTask) -> None:
    if not task.source_file:
        raise ValueError(f"Repository fixture {task.fixture} requires source_file")
    if task.fixture == "repo:aether-self":
        source = REPO_ROOT / task.source_file
        if not source.exists():
            raise FileNotFoundError(f"Repository fixture source not found: {source}")
        destination = workdir / task.source_file
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return
    if task.fixture.startswith("repo:git:"):
        checkout_git_repository(workdir, task)
        return
    raise ValueError(f"Unknown repository fixture: {task.fixture}")


def checkout_git_repository(workdir: Path, task: BenchmarkTask) -> None:
    if not task.repository_manifest:
        raise ValueError(f"External repository task {task.task_id} requires repository_manifest")

    manifest_path = REPO_ROOT / task.repository_manifest
    manifest = load_json(manifest_path)
    source = manifest.get("source", {})
    if source.get("type") != "git":
        raise ValueError(f"Repository manifest {task.repository_manifest} is not a git source")
    if CURRENT_ARGS is None or not CURRENT_ARGS.allow_network_repos:
        raise RuntimeError(
            "External git repository benchmarks require --allow-network-repos "
            f"for manifest {task.repository_manifest}"
        )

    url = str(source["url"])
    commit = str(source["commit"])
    if re.fullmatch(r"[0-9a-fA-F]{40}", commit) is None:
        raise ValueError(f"Repository manifest {task.repository_manifest} must pin a 40-character commit SHA")

    cache_root = repository_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_key = repository_cache_key(task.repository, commit)
    cached_checkout = cache_root / cache_key
    marker = cached_checkout / ".aether-benchmark-cache.json"

    if not repository_cache_is_valid(marker, url, commit):
        remove_repository_cache_entry(cache_root, cached_checkout)
        staged_checkout = Path(tempfile.mkdtemp(prefix=f".{cache_key}-", dir=str(cache_root)))
        try:
            run_git_with_retries(
                ["git", "clone", "--quiet", "--no-tags", "--depth", "1", url, str(staged_checkout)],
                cwd=REPO_ROOT,
                timeout=120,
            )
            run_git_with_retries(
                ["git", "fetch", "--quiet", "--depth", "1", "origin", commit],
                cwd=staged_checkout,
                timeout=120,
            )
            run_git_with_retries(
                ["git", "checkout", "--quiet", commit],
                cwd=staged_checkout,
                timeout=60,
            )
            (staged_checkout / ".aether-benchmark-cache.json").write_text(
                json.dumps({"url": url, "commit": commit}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            try:
                staged_checkout.replace(cached_checkout)
            except OSError:
                if repository_cache_is_valid(marker, url, commit):
                    shutil.rmtree(staged_checkout, ignore_errors=True)
                else:
                    raise
        except Exception:
            shutil.rmtree(staged_checkout, ignore_errors=True)
            raise

    shutil.copytree(
        cached_checkout,
        workdir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".aether-benchmark-cache.json"),
    )


def repository_cache_root() -> Path:
    configured = os.environ.get("AETHER_BENCHMARK_REPO_CACHE")
    if configured:
        return Path(configured).resolve()
    return REPO_ROOT / ".tmp" / "benchmark-repositories"


def repository_cache_key(repository: str, commit: str) -> str:
    safe_repository = re.sub(r"[^A-Za-z0-9_.-]+", "-", repository).strip("-._")
    return f"{safe_repository or 'repository'}-{commit.lower()}"


def repository_cache_is_valid(marker: Path, url: str, commit: str) -> bool:
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"url": url, "commit": commit}


def remove_repository_cache_entry(cache_root: Path, entry: Path) -> None:
    if not entry.exists():
        return
    if entry.resolve().parent != cache_root.resolve():
        raise RuntimeError(f"Refusing to remove repository cache outside {cache_root}")
    shutil.rmtree(entry)


def run_git_with_retries(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    attempts: int = 3,
) -> None:
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        last_result = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if last_result.returncode == 0:
            return
        if attempt < attempts:
            time.sleep(float(attempt))
    detail = (last_result.stderr or last_result.stdout).strip() if last_result else "unknown error"
    raise RuntimeError(
        f"Git command failed after {attempts} attempts ({' '.join(command[:3])}): {detail}"
    )


def write_python_project(workdir: Path, fixture: str) -> None:
    fixtures = {
        "basic_math": (
            "def add(a, b):\n"
            "    return a + b\n\n"
            "def total():\n"
            "    return 0\n\n"
            "if __name__ == '__main__':\n"
            "    print(f'total={total()}')\n"
        ),
        "imports": (
            "import sys\n\n"
            "def shout(name):\n"
            "    return name.upper()\n\n"
            "if __name__ == '__main__':\n"
            "    print(f'name={shout(\"ada\")}')\n"
        ),
        "unused_import": (
            "import os\n"
            "import sys\n\n"
            "def value():\n"
            "    return len(sys.argv) + 2\n\n"
            "if __name__ == '__main__':\n"
            "    print(f'value={value()}')\n"
        ),
        "block": (
            "def value():\n"
            "    value = 1\n"
            "    value = 2\n"
            "    return value\n\n"
            "if __name__ == '__main__':\n"
            "    print(f'value={value()}')\n"
        ),
    }
    if fixture not in fixtures:
        raise ValueError(f"Unknown benchmark fixture: {fixture}")
    write_text_lf(workdir / "app.py", fixtures[fixture])


def write_javascript_project(workdir: Path, fixture: str) -> None:
    fixtures = {
        "js_math": (
            "function add(a, b) {\n"
            "  return a + b;\n"
            "}\n\n"
            "function total() {\n"
            "  return 0;\n"
            "}\n\n"
            "console.log(`total=${total()}`);\n"
        ),
        "js_add_function": (
            "function add(a, b) {\n"
            "  return a + b;\n"
            "}\n\n"
            "console.log(`total=${total()}`);\n"
        ),
        "js_unused_function": (
            "function unused() {\n"
            "  return 99;\n"
            "}\n\n"
            "function keep() {\n"
            "  return 3;\n"
            "}\n\n"
            "console.log(`value=${keep()}`);\n"
        ),
        "js_block": (
            "function value() {\n"
            "  let value = 1;\n"
            "  value = 2;\n"
            "  return value;\n"
            "}\n\n"
            "console.log(`value=${value()}`);\n"
        ),
        "js_large_math": (
            "function add(a, b) {\n"
            "  return a + b;\n"
            "}\n\n"
            "function scale(value, factor) {\n"
            "  return value * factor;\n"
            "}\n\n"
            "function clamp(value, min, max) {\n"
            "  return Math.max(min, Math.min(max, value));\n"
            "}\n\n"
            "function mean(values) {\n"
            "  return values.reduce((sum, value) => sum + value, 0) / values.length;\n"
            "}\n\n"
            "function variance(values) {\n"
            "  const average = mean(values);\n"
            "  return mean(values.map(value => (value - average) ** 2));\n"
            "}\n\n"
            "function normalize(values) {\n"
            "  const average = mean(values);\n"
            "  const spread = Math.sqrt(variance(values)) || 1;\n"
            "  return values.map(value => (value - average) / spread);\n"
            "}\n\n"
            "function movingAverage(values, width) {\n"
            "  return values.map((_, index) => mean(values.slice(Math.max(0, index - width + 1), index + 1)));\n"
            "}\n\n"
            "function describe(values) {\n"
            "  return {mean: mean(values), variance: variance(values), normalized: normalize(values)};\n"
            "}\n\n"
            "function total() {\n"
            "  return 0;\n"
            "}\n\n"
            "console.log(`total=${total()}`);\n"
        ),
        "js_large_block": (
            "function add(a, b) {\n"
            "  return a + b;\n"
            "}\n\n"
            "function scale(value, factor) {\n"
            "  return value * factor;\n"
            "}\n\n"
            "function clamp(value, min, max) {\n"
            "  return Math.max(min, Math.min(max, value));\n"
            "}\n\n"
            "function mean(values) {\n"
            "  return values.reduce((sum, value) => sum + value, 0) / values.length;\n"
            "}\n\n"
            "function variance(values) {\n"
            "  const average = mean(values);\n"
            "  return mean(values.map(value => (value - average) ** 2));\n"
            "}\n\n"
            "function normalize(values) {\n"
            "  const average = mean(values);\n"
            "  const spread = Math.sqrt(variance(values)) || 1;\n"
            "  return values.map(value => (value - average) / spread);\n"
            "}\n\n"
            "function movingAverage(values, width) {\n"
            "  return values.map((_, index) => mean(values.slice(Math.max(0, index - width + 1), index + 1)));\n"
            "}\n\n"
            "function describe(values) {\n"
            "  return {mean: mean(values), variance: variance(values), normalized: normalize(values)};\n"
            "}\n\n"
            "function value() {\n"
            "  let value = 1;\n"
            "  value = 2;\n"
            "  return value;\n"
            "}\n\n"
            "console.log(`value=${value()}`);\n"
        ),
    }
    if fixture not in fixtures:
        raise ValueError(f"Unknown benchmark fixture: {fixture}")
    write_text_lf(workdir / "app.js", fixtures[fixture])


def write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def stringify_error(item: Any) -> str:
    message = getattr(item, "message", None)
    rule = getattr(item, "rule", None)
    if rule and message:
        return f"{rule}: {message}"
    if message:
        return str(message)
    return str(item)


def apply_control_edit(workdir: Path, task: BenchmarkTask) -> None:
    path = workdir / source_file_for(task)
    source = path.read_text(encoding="utf-8")
    if task.task_id == "py-modify-function":
        source = source.replace(
            "def total():\n    return 0\n",
            "def total():\n    return add(20, 22)\n",
        )
    elif task.task_id == "py-add-import":
        source = "import json\nfrom pathlib import Path\n" + source
    elif task.task_id == "py-remove-import":
        source = source.replace("import os\n", "")
    elif task.task_id == "py-replace-block":
        source = source.replace("    value = 2\n", "    value = 42\n")
    elif task.task_id == "py-fi-control-syntax-error":
        source = source.replace("def total():\n    return 0\n", "def total():\n    return (1 +\n")
    elif task.task_id == "py-fi-runtime-error":
        source = source.replace(
            "def total():\n    return 0\n",
            "def total():\n    raise RuntimeError('injected failure')\n",
        )
    elif task.task_id == "py-fi-broken-import":
        source = "import definitely_missing_aether_dependency\n" + source
    elif task.task_id == "py-fi-timeout":
        source = source.replace(
            "def total():\n    return 0\n",
            "def total():\n    import time\n    time.sleep(2)\n    return 0\n",
        )
    elif task.task_id == "js-modify-function":
        source = source.replace(
            "function total() {\n  return 0;\n}",
            "function total() {\n  return add(20, 22);\n}",
        )
    elif task.task_id == "js-add-function":
        source += "\nfunction total() {\n  return add(20, 22);\n}\n"
    elif task.task_id == "js-remove-function":
        source = source.replace("function unused() {\n  return 99;\n}\n\n", "")
    elif task.task_id == "js-replace-block":
        source = source.replace("  value = 2;\n", "  value = 42;\n")
    elif task.task_id == "js-fi-control-syntax-error":
        source = source.replace(
            "function total() {\n  return 0;\n}",
            "function total() {\n  return (1 +;\n}",
        )
    elif task.task_id == "js-fi-runtime-error":
        source = source.replace(
            "function total() {\n  return 0;\n}",
            "function total() {\n  throw new Error('injected failure');\n}",
        )
    elif task.task_id == "js-fi-broken-import":
        source = "const definitelyMissingAetherDependency = require('definitely_missing_aether_dependency');\n" + source
    elif task.task_id == "js-fi-timeout":
        source = source.replace(
            "function total() {\n  return 0;\n}",
            "function total() {\n  while (true) {}\n}",
        )
    else:
        raise ValueError(f"Control mode is not implemented for task {task.task_id}")
    path.write_text(source, encoding="utf-8")


def output_matches(task: BenchmarkTask, stdout: str) -> bool:
    return task.expected_stdout is None or task.expected_stdout in stdout


def content_matches(task: BenchmarkTask, content: str) -> bool:
    return all(item in content for item in task.expected_content) and all(
        item not in content for item in task.expected_absent_content
    )


def source_file_for(task: BenchmarkTask) -> str:
    if task.source_file:
        return task.source_file
    return "app.js" if task.language == "javascript" else "app.py"


def check_syntax(path: Path, language: str) -> tuple[bool, str]:
    if language == "python":
        return compile_python(path)
    if language == "javascript":
        return check_javascript(path)
    raise ValueError(f"Unsupported syntax check language: {language}")


def compile_python(path: Path) -> tuple[bool, str]:
    try:
        py_compile.compile(str(path), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)


def check_javascript(path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["node", "--check", str(path)],
        cwd=str(path.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0, result.stderr.strip()


def run_program(path: Path, language: str, timeout_ms: int) -> subprocess.CompletedProcess[str]:
    if language == "python":
        return run_python(path, timeout_ms)
    if language == "javascript":
        return run_javascript(path, timeout_ms)
    raise ValueError(f"Unsupported runtime language: {language}")


def run_verification(
    workdir: Path,
    source_path: Path,
    task: BenchmarkTask,
) -> subprocess.CompletedProcess[str]:
    if task.category in {"real_repository", "external_repository", "external_agent_patch"}:
        return run_test_command(workdir, task.test_command, task.timeout_ms)
    return run_program(source_path, task.language, task.timeout_ms)


def run_test_command(
    workdir: Path,
    command: str,
    timeout_ms: int,
) -> subprocess.CompletedProcess[str]:
    command_parts = shlex.split(command, posix=True)
    if command_parts and command_parts[0] in {"python", "python3"}:
        command_parts[0] = sys.executable
        return subprocess.run(
            command_parts,
            cwd=str(workdir),
            text=True,
            capture_output=True,
            timeout=timeout_ms / 1000,
            check=False,
        )
    if command_parts and command_parts[0] == "node":
        return subprocess.run(
            command_parts,
            cwd=str(workdir),
            text=True,
            capture_output=True,
            timeout=timeout_ms / 1000,
            check=False,
        )
    return subprocess.run(
        command,
        cwd=str(workdir),
        text=True,
        capture_output=True,
        timeout=timeout_ms / 1000,
        check=False,
        shell=True,
    )


def run_python(path: Path, timeout_ms: int) -> subprocess.CompletedProcess[str]:
    return run_process([sys.executable, str(path)], path.parent, timeout_ms)


def run_javascript(path: Path, timeout_ms: int) -> subprocess.CompletedProcess[str]:
    return run_process(["node", str(path)], path.parent, timeout_ms)


def run_process(args: list[str], cwd: Path, timeout_ms: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_ms / 1000,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=f"Timed out after {timeout_ms}ms",
        )


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    successes = sum(1 for record in records if record["task_success"])
    failures = total - successes
    modes = sorted({r["configuration"]["mode"] for r in records})
    languages = sorted({r["language"] for r in records})
    return {
        "records": total,
        "task_successes": successes,
        "task_failures": failures,
        "success_rate": round(successes / total, 6) if total else None,
        "metrics": correctness_metrics(records),
        "by_mode": {
            mode: summarize([r for r in records if r["configuration"]["mode"] == mode])
            for mode in modes
        }
        if len(modes) > 1
        else {},
        "by_language": {
            language: summarize([r for r in records if r["language"] == language])
            for language in languages
        }
        if len(languages) > 1
        else {},
    }


def correctness_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid_categories = {
        "valid_transformation",
        "real_repository",
        "external_repository",
        "agent_patch",
        "external_agent_patch",
    }
    valid = [r for r in records if r["configuration"].get("category") in valid_categories]
    invalid = [
        r for r in records
        if r["configuration"].get("category") == "invalid_patch"
        and r["configuration"].get("mode") == "aether"
    ]
    invalid_tested = [r for r in invalid if patch_was_tested(r)]
    injected = [r for r in records if r["configuration"].get("category") == "failure_injection"]
    rollback_attempts = [r for r in records if r["rollback_triggered"]]
    rollback_expected = [r for r in records if r["configuration"].get("category") == "rollback"]

    invalid_detected = [
        r for r in invalid_tested
        if r["validation_failed"] or r["error_type"] in {"validation_failed", "aether_apply_failed"}
    ]
    false_acceptances = [
        r for r in invalid_tested
        if not r["validation_failed"] and r["error_type"] is None
    ]

    return {
        "transformation_success_rate": rate(
            sum(1 for r in valid if r["task_success"]),
            len(valid),
        ),
        "invalid_patch_detection_rate": rate(len(invalid_detected), len(invalid_tested)),
        "false_acceptance_rate": rate(len(false_acceptances), len(invalid_tested)),
        "rollback_success_rate": rate(
            sum(1 for r in rollback_attempts if r["rollback_success"] is True),
            len(rollback_attempts),
        ),
        "expected_rollback_detection_rate": rate(
            sum(1 for r in rollback_expected if r["rollback_triggered"]),
            len(rollback_expected),
        ),
        "failure_detection_rate": rate(
            sum(1 for r in injected if r.get("failure_detected")),
            len(injected),
        ),
    }


def patch_was_tested(record: dict[str, Any]) -> bool:
    return (
        record.get("patch_size") is not None
        or record.get("validation_failed") is True
        or record.get("rollback_triggered") is True
        or record.get("execution_time_ms") is not None
    )


def classify_provider_error(error_detail: str | None) -> dict[str, Any]:
    if not error_detail:
        return {
            "provider_error_type": None,
            "provider_status_code": None,
            "provider_retryable": None,
            "provider_quota_exhausted": False,
        }
    provider = None
    lowered = error_detail.lower()
    for name in ["gemini", "openrouter", "openai"]:
        if f"{name} api error" in lowered or f"{name} transient error" in lowered:
            provider = name
            break
    status_code = None
    match = re.search(r"(?:API|transient) error (\d{3})", error_detail)
    if match:
        status_code = int(match.group(1))
    quota_exhausted = any(
        marker in lowered
        for marker in [
            "resource_exhausted",
            "quota exceeded",
            "quota_exhausted",
            "per day",
            "daily",
            "free-models-per-day",
            "insufficient credits",
        ]
    )
    retryable = status_code in {429, 500, 502, 503, 504} and not quota_exhausted if status_code else None
    if provider is None and status_code is None and not quota_exhausted:
        return {
            "provider_error_type": None,
            "provider_status_code": None,
            "provider_retryable": None,
            "provider_quota_exhausted": False,
        }
    kind = provider or "provider"
    if quota_exhausted:
        kind = f"{kind}_quota_exhausted"
    elif status_code == 429:
        kind = f"{kind}_rate_limited"
    elif status_code is not None:
        kind = f"{kind}_http_{status_code}"
    return {
        "provider_error_type": kind,
        "provider_status_code": status_code,
        "provider_retryable": retryable,
        "provider_quota_exhausted": quota_exhausted,
    }


def rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if (
            rel.startswith(".ai_runtime/")
            or rel.startswith(".git/")
            or rel in {".agent-task.json", ".benchmark-patch.json"}
            or "__pycache__/" in rel
            or rel.endswith(".pyc")
        ):
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def tree_size(root: Path) -> tuple[int, int]:
    total_bytes = 0
    file_count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if (
            rel.startswith(".ai_runtime/")
            or rel.startswith(".git/")
            or rel in {".agent-task.json", ".benchmark-patch.json"}
            or "__pycache__/" in rel
            or rel.endswith(".pyc")
        ):
            continue
        total_bytes += path.stat().st_size
        file_count += 1
    return total_bytes, file_count


def git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def environment_metadata() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "machine": platform.machine(),
        "cwd": str(REPO_ROOT),
        "env": {
            "CI": os.environ.get("CI"),
            "AETHER_BENCHMARK_VERSION": os.environ.get("AETHER_BENCHMARK_VERSION"),
        },
    }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def rounded_ms(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
