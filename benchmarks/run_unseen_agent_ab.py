#!/usr/bin/env python
"""Run command-backed raw-vs-Aether trials on hidden-test coding tasks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import run as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "tasks" / "unseen_agent_smoke.json"
ARMS = ("raw_full_file", "aether_patch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--raw-command", nargs="+", required=True)
    parser.add_argument("--aether-command", nargs="+", required=True)
    parser.add_argument("--agent-retries", type=int, default=0)
    parser.add_argument("--agent-timeout-ms", type=int, default=120000)
    parser.add_argument("--allow-network-repos", action="store_true")
    parser.add_argument("--keep-workdirs", action="store_true")
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be >= 1")
    if args.agent_retries < 0:
        parser.error("--agent-retries must be >= 0")

    base.CURRENT_ARGS = argparse.Namespace(allow_network_repos=args.allow_network_repos)
    manifest = load_json(args.manifest)
    tasks = [make_task(item) for item in manifest["tasks"]]
    records: list[dict[str, Any]] = []
    baseline = baseline_original_passes(tasks, keep=args.keep_workdirs)

    for trial in range(1, args.trials + 1):
        for task in tasks:
            records.append(run_arm(task, "raw_full_file", args.raw_command, trial, args))
            records.append(run_arm(task, "aether_patch", args.aether_command, trial, args))

    output = {
        "report_version": "unseen-agent-ab-v1",
        "experiment_id": args.experiment_id,
        "commit_sha": base.git_commit_sha(),
        "manifest": str(args.manifest.relative_to(ROOT)).replace("\\", "/") if args.manifest.is_relative_to(ROOT) else str(args.manifest),
        "manifest_sha256": file_hash(args.manifest),
        "trials": args.trials,
        "task_count": len(tasks),
        "baseline_original_passes": baseline,
        "records": records,
        "summary": summarize(records),
        "limitations": [
            "The smoke manifest is deterministic and public; it validates the protocol but is not itself a final unseen benchmark.",
            "Real unseen evidence requires fresh private manifests and live/model-backed commands.",
            "Token counts use local estimates unless provider usage is returned by the agent command.",
        ],
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
    return 0 if all(item["task_success"] for item in records) else 1


def run_arm(
    task: base.BenchmarkTask,
    arm: str,
    command: list[str],
    trial: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix=f"aether-unseen-{task.task_id}-{arm}-"))
    started = time.perf_counter()
    generation: dict[str, Any] = {}
    agent_error: str | None = None
    applicable = False
    validation_failed = False
    rollback_triggered = False
    syntax_valid = False
    hidden_test_pass = False
    repository_corrupted = False
    error_type: str | None = None
    error_detail: str | None = None
    verification_time_ms: float | None = None
    application_time_ms: float | None = None
    generated_text = ""
    try:
        base.write_project(workdir, task)
        source_path = workdir / base.source_file_for(task)
        before_hash = base.tree_hash(workdir)
        descriptor = build_descriptor(task, workdir, arm, trial)
        generation = run_agent_command(command, descriptor, args)
        artifact = generation.get("content") if arm == "raw_full_file" else generation.get("patch")
        apply_started = time.perf_counter()
        if arm == "raw_full_file":
            if not isinstance(artifact, str):
                raise ValueError("raw_full_file agent must return a string content field")
            generated_text = artifact
            atomic_write(source_path, artifact)
            applicable = True
        else:
            if not isinstance(artifact, dict):
                raise ValueError("aether_patch agent must return a patch object")
            generated_text = json.dumps(artifact, sort_keys=True)
            result = base.apply_aether_patch(workdir, artifact)
            applicable = bool(result["ok"])
            validation_failed = bool(result["validation_failed"])
            rollback_triggered = bool(result["rolled_back"])
            if not applicable:
                error_type = "validation_failed" if validation_failed else "aether_apply_failed"
                error_detail = "; ".join(base.stringify_error(item) for item in result["errors"])
        application_time_ms = base.elapsed_ms(apply_started)
        syntax_valid, syntax_detail = base.check_syntax(source_path, task.language)
        if not syntax_valid:
            error_type = error_type or "syntax_error"
            error_detail = error_detail or syntax_detail
        verify_started = time.perf_counter()
        verification = base.run_verification(workdir, source_path, task)
        verification_time_ms = base.elapsed_ms(verify_started)
        hidden_test_pass = verification.returncode == 0 and base.output_matches(task, verification.stdout)
        if not hidden_test_pass:
            error_type = error_type or "hidden_test_failed"
            error_detail = error_detail or verification.stderr.strip() or verification.stdout.strip()
        repository_corrupted = not hidden_test_pass and base.tree_hash(workdir) != before_hash
    except Exception as exc:
        agent_error = str(exc)
        error_type = error_type or type(exc).__name__
        error_detail = error_detail or str(exc)
    finally:
        if not args.keep_workdirs:
            shutil.rmtree(workdir, ignore_errors=True)

    usage = generation.get("usage") if isinstance(generation.get("usage"), dict) else {}
    descriptor_tokens = base.count_tokens(json.dumps(build_descriptor(task, Path("."), arm, trial, include_source=False), sort_keys=True))
    input_tokens = usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else descriptor_tokens
    output_tokens = usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else base.count_tokens(generated_text)
    return {
        "task_id": task.task_id,
        "pair_id": f"{task.task_id}:trial-{trial}",
        "trial": trial,
        "arm": arm,
        "language": task.language,
        "repository": task.repository,
        "source_file": base.source_file_for(task),
        "task_success": applicable and syntax_valid and hidden_test_pass,
        "applicable": applicable,
        "validation_failed": validation_failed,
        "rollback_triggered": rollback_triggered,
        "syntax_valid": syntax_valid,
        "hidden_test_pass": hidden_test_pass,
        "repository_corrupted_on_failure": repository_corrupted,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "output_bytes": len(generated_text.encode("utf-8")),
        "token_estimator": base.token_estimator_name(),
        "agent_attempts": generation.get("attempt_count"),
        "agent_latency_ms": generation.get("agent_latency_ms"),
        "agent_cost_usd": usage.get("cost_usd"),
        "model": usage.get("model"),
        "application_time_ms": round(application_time_ms, 6) if application_time_ms is not None else None,
        "verification_time_ms": round(verification_time_ms, 6) if verification_time_ms is not None else None,
        "total_task_time_ms": round(base.elapsed_ms(started), 6),
        "artifact_sha256": hash_text(generated_text) if generated_text else None,
        "error_type": error_type,
        "error_detail": error_detail,
        "agent_error": agent_error,
    }


def build_descriptor(
    task: base.BenchmarkTask,
    workdir: Path,
    arm: str,
    trial: int,
    *,
    include_source: bool = True,
) -> dict[str, Any]:
    source = ""
    if include_source:
        source_path = workdir / base.source_file_for(task)
        source = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    contract = (
        "Return JSON with content containing the complete updated source file."
        if arm == "raw_full_file"
        else "Return JSON with patch containing an Aether 1.0 patch for the requested source file."
    )
    return {
        "protocol_version": "unseen-agent-ab-v1",
        "descriptor_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{task.task_id}:{trial}:{arm}")),
        "task_id": task.task_id,
        "trial": trial,
        "arm": arm,
        "language": task.language,
        "repository": task.repository,
        "fixture": task.fixture,
        "source_file": base.source_file_for(task),
        "description": task.description,
        "source": source,
        "output_contract": contract,
        "withheld": ["test_command", "expected_outputs", "reference_solution"],
    }


def run_agent_command(command: list[str], descriptor: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    attempts = []
    started = time.perf_counter()
    payload = json.dumps(descriptor, sort_keys=True)
    for attempt in range(1, args.agent_retries + 2):
        attempt_started = time.perf_counter()
        try:
            result = subprocess.run(
                command,
                input=payload,
                text=True,
                capture_output=True,
                timeout=args.agent_timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            attempts.append({
                "attempt": attempt,
                "ok": False,
                "elapsed_ms": base.elapsed_ms(attempt_started),
                "error": f"timeout after {args.agent_timeout_ms}ms",
                "stdout": exc.stdout,
                "stderr": exc.stderr,
            })
            continue
        attempts.append({
            "attempt": attempt,
            "ok": result.returncode == 0,
            "elapsed_ms": base.elapsed_ms(attempt_started),
            "returncode": result.returncode,
            "stderr": result.stderr,
        })
        if result.returncode != 0:
            continue
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            attempts[-1]["ok"] = False
            attempts[-1]["error"] = f"invalid JSON: {exc}"
            continue
        if not isinstance(envelope, dict):
            attempts[-1]["ok"] = False
            attempts[-1]["error"] = "agent output must be a JSON object"
            continue
        envelope["attempt_count"] = attempt
        envelope["attempts"] = attempts
        envelope["agent_latency_ms"] = base.elapsed_ms(started)
        return envelope
    raise RuntimeError(f"agent failed all attempts: {attempts}")


def make_task(item: dict[str, Any]) -> base.BenchmarkTask:
    return base.BenchmarkTask(
        task_id=item["task_id"],
        language=item["language"],
        repository=item["repository"],
        fixture=item["fixture"],
        source_file=item.get("source_file"),
        repository_manifest=item.get("repository_manifest"),
        category=item.get("category", "unseen_agent_ab"),
        failure_type=None,
        description=item["description"],
        test_command=item["test_command"],
        verification_level=str(item.get("verification_level", "hidden_behavior")),
        timeout_ms=int(item.get("timeout_ms", 30000)),
        supported_modes=[],
        expected_success=True,
        expected_stdout=item.get("expected_stdout"),
        expected_content=[],
        expected_absent_content=[],
        expected_error_type=None,
        expected_rollback=None,
        expected_failure_detected=None,
        patch={},
    )


def baseline_original_passes(tasks: list[base.BenchmarkTask], *, keep: bool) -> list[str]:
    passing = []
    for task in tasks:
        workdir = Path(tempfile.mkdtemp(prefix=f"aether-unseen-baseline-{task.task_id}-"))
        try:
            base.write_project(workdir, task)
            source = workdir / base.source_file_for(task)
            verification = base.run_verification(workdir, source, task)
            if verification.returncode == 0 and base.output_matches(task, verification.stdout):
                passing.append(task.task_id)
        finally:
            if not keep:
                shutil.rmtree(workdir, ignore_errors=True)
    return passing


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm = {}
    for arm in ARMS:
        selected = [item for item in records if item["arm"] == arm]
        by_arm[arm] = {
            "records": len(selected),
            "successes": sum(bool(item["task_success"]) for item in selected),
            "success_rate": round(sum(bool(item["task_success"]) for item in selected) / len(selected), 6) if selected else 0,
            "output_tokens": sum(int(item["output_tokens"] or 0) for item in selected),
            "output_bytes": sum(int(item["output_bytes"] or 0) for item in selected),
            "mean_total_task_time_ms": round(sum(float(item["total_task_time_ms"]) for item in selected) / len(selected), 6) if selected else None,
        }
    raw_tokens = by_arm["raw_full_file"]["output_tokens"]
    aether_tokens = by_arm["aether_patch"]["output_tokens"]
    raw_bytes = by_arm["raw_full_file"]["output_bytes"]
    aether_bytes = by_arm["aether_patch"]["output_bytes"]
    return {
        "records": len(records),
        "pairs": len({item["pair_id"] for item in records}),
        "by_arm": by_arm,
        "aether_output_token_savings_pct": pct(raw_tokens, aether_tokens),
        "aether_output_byte_savings_pct": pct(raw_bytes, aether_bytes),
        "aether_success_delta_percentage_points": round(
            (by_arm["aether_patch"]["success_rate"] - by_arm["raw_full_file"]["success_rate"]) * 100,
            6,
        ),
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(records[0]) if records else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.unseen.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def pct(left: float, right: float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
