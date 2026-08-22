#!/usr/bin/env python
"""Grade blind Aether-patch and full-file agent outputs on matched hidden tasks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import run as base
from agents.paired_blind_protocol import (
    ARMS,
    FULL_FILE_ARM,
    PATCH_ARM,
    arm_descriptor,
    build_prompt_core,
    canonical_hash,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "tasks" / "paired_agent_unseen.json"
DEFAULT_TRIAL_DIR = ROOT / "benchmarks" / "agents" / "paired_blind_trials"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--trials-dir", type=Path, default=DEFAULT_TRIAL_DIR)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument(
        "--allow-network-repos",
        action="store_true",
        help="Allow pinned external git repository fixtures to be cloned or refreshed.",
    )
    parser.add_argument("--keep-workdirs", action="store_true")
    args = parser.parse_args()
    base.CURRENT_ARGS = argparse.Namespace(allow_network_repos=args.allow_network_repos)

    manifest = load_object(args.manifest)
    task_values = manifest.get("tasks")
    if not isinstance(task_values, list) or not task_values:
        raise ValueError("Paired manifest must contain tasks")
    tasks = {item["task_id"]: make_task(item) for item in task_values}
    sources = load_sources(tasks, task_values)
    core = build_prompt_core(task_values, sources)
    core_hash = canonical_hash(core)
    core_tokens = base.count_tokens(json.dumps(core, sort_keys=True))
    generations = load_generations(args.trials_dir, tasks)
    baseline = baseline_failures(tasks, args.keep_workdirs)

    records: list[dict[str, Any]] = []
    for generation in generations:
        task = tasks[generation["task"]]
        records.append(
            grade(
                task,
                generation,
                core_hash=core_hash,
                core_tokens=core_tokens,
                experiment_id=args.experiment_id,
                keep_workdir=args.keep_workdirs,
            )
        )

    output = {
        "report_version": "paired-blind-agent-v1",
        "experiment_id": args.experiment_id,
        "commit_sha": base.git_commit_sha(),
        "manifest_sha256": file_hash(args.manifest),
        "prompt_core_sha256": core_hash,
        "generation_isolation": "fresh stateless subagents; prompt-enforced source-only restriction",
        "baseline_original_passes": baseline,
        "records": records,
    }
    raw = ROOT / "benchmarks" / "results" / "raw" / f"{args.experiment_id}.json"
    processed = ROOT / "benchmarks" / "results" / "processed" / f"{args.experiment_id}.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    processed.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(processed, records)
    print(f"Wrote raw results: {raw.relative_to(ROOT)}")
    print(f"Wrote CSV results: {processed.relative_to(ROOT)}")
    print(json.dumps(summary(records, baseline), indent=2, sort_keys=True))
    return 0


def load_generations(directory: Path, tasks: dict[str, base.BenchmarkTask]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for path in sorted(directory.glob("*-trial-*.json")):
        raw = path.read_bytes()
        payload = json.loads(raw)
        arm = payload.get("arm")
        trial = payload.get("trial")
        values = payload.get("records")
        if arm not in ARMS or not isinstance(trial, int) or trial < 1 or not isinstance(values, list):
            raise ValueError(f"Invalid paired trial envelope: {path}")
        for value in values:
            task_id = value.get("task") if isinstance(value, dict) else None
            key = (str(task_id), trial, arm)
            if task_id not in tasks or key in seen:
                raise ValueError(f"Unknown or duplicate paired generation {key} in {path}")
            artifact = value.get("patch") if arm == PATCH_ARM else value.get("content")
            if arm == PATCH_ARM and not isinstance(artifact, dict):
                raise ValueError(f"Patch artifact missing for {key}")
            if arm == FULL_FILE_ARM and not isinstance(artifact, str):
                raise ValueError(f"Full-file artifact missing for {key}")
            if arm == FULL_FILE_ARM and value.get("source_file") != tasks[task_id].source_file:
                raise ValueError(f"Full-file target mismatch for {key}")
            seen.add(key)
            records.append({
                "task": task_id,
                "trial": trial,
                "arm": arm,
                "artifact": artifact,
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_response_file": str(path.relative_to(ROOT)).replace("\\", "/"),
            })
    expected = {(task, trial, arm) for task in tasks for trial in range(1, 4) for arm in ARMS}
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise ValueError(f"Paired generation coverage mismatch; missing={missing}, extra={extra}")
    return sorted(records, key=lambda item: (item["trial"], item["task"], item["arm"]))


def grade(
    task: base.BenchmarkTask,
    generation: dict[str, Any],
    *,
    core_hash: str,
    core_tokens: int,
    experiment_id: str,
    keep_workdir: bool,
) -> dict[str, Any]:
    arm = generation["arm"]
    trial = generation["trial"]
    artifact = generation["artifact"]
    workdir = Path(tempfile.mkdtemp(prefix=f"aether-paired-{task.task_id}-{arm}-"))
    application_started = time.perf_counter()
    applicable = False
    validation_failed = False
    rolled_back = False
    error_type: str | None = None
    error_detail: str | None = None
    syntax_valid = False
    hidden_test_pass = False
    try:
        base.write_project(workdir, task)
        source_path = workdir / base.source_file_for(task)
        original = source_path.read_text(encoding="utf-8")
        before_hash = base.tree_hash(workdir)
        edit_started = time.perf_counter()
        if arm == PATCH_ARM:
            result = base.apply_aether_patch(workdir, artifact)
            applicable = bool(result["ok"])
            validation_failed = bool(result["validation_failed"])
            rolled_back = bool(result["rolled_back"])
            if not applicable:
                error_type = "validation_failed" if validation_failed else "aether_apply_failed"
                error_detail = "; ".join(base.stringify_error(item) for item in result["errors"])
        else:
            atomic_write(source_path, artifact)
            applicable = True
        application_time_ms = base.elapsed_ms(edit_started)

        syntax_valid, syntax_detail = base.check_syntax(source_path, task.language)
        if not syntax_valid:
            error_type = error_type or "syntax_error"
            error_detail = error_detail or syntax_detail
        verify_started = time.perf_counter()
        verification = base.run_verification(workdir, source_path, task)
        verification_time_ms = base.elapsed_ms(verify_started)
        hidden_test_pass = verification.returncode == 0
        if not hidden_test_pass:
            error_type = error_type or "hidden_test_failed"
            error_detail = error_detail or verification.stderr.strip() or verification.stdout.strip()
        final = source_path.read_text(encoding="utf-8")
        repository_corrupted = not hidden_test_pass and base.tree_hash(workdir) != before_hash
        unchanged_ratio = unchanged_line_ratio(original, final)
    except Exception as exc:
        application_time_ms = base.elapsed_ms(application_started)
        verification_time_ms = None
        repository_corrupted = False
        unchanged_ratio = None
        error_type = type(exc).__name__
        error_detail = str(exc)
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    descriptor = arm_descriptor(core_hash, arm, trial)
    rendered = json.dumps(artifact, sort_keys=True) if arm == PATCH_ARM else artifact
    format_valid = patch_format_valid(artifact) if arm == PATCH_ARM else isinstance(artifact, str)
    return {
        "experiment_id": experiment_id,
        "pair_id": f"{task.task_id}:trial-{trial}",
        "task_id": task.task_id,
        "trial": trial,
        "artifact_format": arm,
        "language": task.language,
        "repository": task.repository,
        "source_file": task.source_file,
        "prompt_core_sha256": core_hash,
        "arm_descriptor_sha256": canonical_hash(descriptor),
        "raw_response_sha256": generation["raw_response_sha256"],
        "raw_response_file": generation["raw_response_file"],
        "artifact_sha256": canonical_hash(artifact),
        "format_valid": format_valid,
        "applicable": applicable,
        "validation_failed": validation_failed,
        "rollback_triggered": rolled_back,
        "syntax_valid": syntax_valid,
        "hidden_test_pass": hidden_test_pass,
        "task_success": applicable and syntax_valid and hidden_test_pass,
        "output_bytes": len(rendered.encode("utf-8")),
        "estimated_output_tokens": base.count_tokens(rendered),
        "estimated_input_tokens": core_tokens + base.count_tokens(json.dumps(descriptor, sort_keys=True)),
        "token_estimator": base.token_estimator_name(),
        "application_time_ms": round(application_time_ms, 6),
        "verification_time_ms": round(verification_time_ms, 6) if verification_time_ms is not None else None,
        "generation_to_verified_time_ms": None,
        "unchanged_line_ratio": unchanged_ratio,
        "repository_corrupted_on_failure": repository_corrupted,
        "error_type": error_type,
        "error_detail": error_detail,
    }


def baseline_failures(tasks: dict[str, base.BenchmarkTask], keep: bool) -> list[str]:
    passing: list[str] = []
    for task in tasks.values():
        workdir = Path(tempfile.mkdtemp(prefix=f"aether-paired-baseline-{task.task_id}-"))
        try:
            base.write_project(workdir, task)
            source_path = workdir / base.source_file_for(task)
            if base.run_verification(workdir, source_path, task).returncode == 0:
                passing.append(task.task_id)
        finally:
            if not keep:
                shutil.rmtree(workdir, ignore_errors=True)
    return passing


def load_sources(tasks: dict[str, base.BenchmarkTask], values: list[dict[str, Any]]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for value in values:
        source_id = value["source_id"]
        if source_id in sources:
            continue
        task = tasks[value["task_id"]]
        workdir = Path(tempfile.mkdtemp(prefix="aether-paired-source-"))
        try:
            base.write_project(workdir, task)
            sources[source_id] = (workdir / base.source_file_for(task)).read_text(encoding="utf-8")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return sources


def make_task(item: dict[str, Any]) -> base.BenchmarkTask:
    return base.BenchmarkTask(
        task_id=item["task_id"], language=item["language"], repository=item["repository"],
        fixture=item["fixture"], source_file=item["source_file"],
        repository_manifest=item["repository_manifest"], category="external_agent_patch",
        failure_type=None, description=item["description"], test_command=item["test_command"],
        verification_level="hidden_behavior", timeout_ms=int(item.get("timeout_ms", 30000)),
        supported_modes=[], expected_success=True, expected_stdout=None,
        expected_content=[], expected_absent_content=[], expected_error_type=None,
        expected_rollback=None, expected_failure_detected=None, patch={},
    )


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.paired.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def patch_format_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) - {
        "schema_version", "patch_id", "action", "target", "changes", "metadata"
    }:
        return False
    target = value.get("target")
    changes = value.get("changes")
    return (
        value.get("schema_version") == "1.0"
        and isinstance(value.get("patch_id"), str)
        and isinstance(value.get("action"), str)
        and isinstance(target, dict)
        and isinstance(target.get("file"), str)
        and isinstance(changes, dict)
        and isinstance(changes.get("operation"), str)
        and (
            changes.get("operation") != "replace_body"
            or isinstance(changes.get("payload"), str)
        )
    )


def unchanged_line_ratio(before: str, after: str) -> float:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    same = sum(left == right for left, right in zip(before_lines, after_lines))
    return round(same / max(len(before_lines), len(after_lines), 1), 6)


def summary(records: list[dict[str, Any]], baseline: list[str]) -> dict[str, Any]:
    by_arm = {}
    for arm in ARMS:
        selected = [item for item in records if item["artifact_format"] == arm]
        by_arm[arm] = {
            "records": len(selected),
            "successes": sum(item["task_success"] for item in selected),
        }
    return {"records": len(records), "by_arm": by_arm, "baseline_original_passes": baseline}


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(records[0]) if records else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected object in {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
