#!/usr/bin/env python
"""Run a deterministic self-healing loop A/B benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import run as base


ROOT = Path(__file__).resolve().parents[1]
ARMS = ("raw_healing", "aether_healing")


@dataclass(frozen=True)
class HealingTask:
    task_id: str
    failure_type: str
    description: str
    expected_stdout: str
    invalid_repair: bool = False


TASKS = [
    HealingTask(
        task_id="sh-js-schema-key",
        failure_type="schema_mismatch",
        description="Telemetry service rejects a new vibration_hz key that should be allowed.",
        expected_stdout="accepted=true",
    ),
    HealingTask(
        task_id="sh-js-null-user",
        failure_type="none_handling",
        description="User formatter crashes when the profile display name is null.",
        expected_stdout="display=anonymous",
    ),
    HealingTask(
        task_id="sh-js-negative-clamp",
        failure_type="range_validation",
        description="Score normalizer allows negative readings that should clamp to zero.",
        expected_stdout="score=0",
    ),
    HealingTask(
        task_id="sh-js-json-parse",
        failure_type="invalid_json_handling",
        description="JSON parser crashes on malformed payloads instead of returning an empty object.",
        expected_stdout="keys=0",
    ),
    HealingTask(
        task_id="sh-js-cache-fast-path",
        failure_type="performance_evolution",
        description="Expensive Fibonacci calculation should use the existing memoized helper.",
        expected_stdout="fib=55",
    ),
    HealingTask(
        task_id="sh-js-invalid-rollback",
        failure_type="invalid_patch",
        description="The repair agent proposes an unsafe mutation that must not corrupt the repository.",
        expected_stdout="accepted=false",
        invalid_repair=True,
    ),
    HealingTask(
        task_id="sh-js-behavior-break",
        failure_type="behavior_breaking_patch",
        description="The repair agent proposes syntactically valid code that breaks the hidden behavior check.",
        expected_stdout="display=anonymous",
        invalid_repair=True,
    ),
    HealingTask(
        task_id="sh-js-path-traversal",
        failure_type="unsafe_path",
        description="The repair agent tries to mutate outside the project root.",
        expected_stdout="accepted=false",
        invalid_repair=True,
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--keep-workdirs", action="store_true")
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be >= 1")

    records: list[dict[str, Any]] = []
    baseline_failures = baseline_original_failures()
    for trial in range(1, args.trials + 1):
        for task in TASKS:
            for arm in ARMS:
                records.append(run_arm(task, arm, trial, keep=args.keep_workdirs))

    payload = {
        "report_version": "self-healing-ab-v1",
        "experiment_id": args.experiment_id,
        "commit_sha": base.git_commit_sha(),
        "trials": args.trials,
        "task_count": len(TASKS),
        "baseline_original_failures": baseline_failures,
        "records": records,
        "summary": summarize(records),
        "limitations": [
            "This is a deterministic local self-healing benchmark, not a live LLM study.",
            "The benchmark models a self-healing loop around a JavaScript service so it can run without Python SDK dependency friction.",
            "Raw healing intentionally represents direct full-file mutation; Aether healing represents structured patch mutation through validation and snapshot rollback.",
        ],
    }
    raw = ROOT / "benchmarks" / "results" / "raw" / f"{args.experiment_id}.json"
    processed = ROOT / "benchmarks" / "results" / "processed" / f"{args.experiment_id}.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    processed.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(processed, records)
    print(f"Wrote raw results: {raw.relative_to(ROOT)}")
    print(f"Wrote CSV results: {processed.relative_to(ROOT)}")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


def run_arm(task: HealingTask, arm: str, trial: int, *, keep: bool) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix=f"aether-healing-{task.task_id}-{arm}-"))
    started = time.perf_counter()
    generated_text = ""
    applicable = False
    validation_failed = False
    rollback_triggered = False
    syntax_valid = False
    hidden_test_pass = False
    repository_corrupted = False
    error_type: str | None = None
    error_detail: str | None = None
    application_time_ms: float | None = None
    verification_time_ms: float | None = None
    try:
        write_project(workdir, task)
        before_hash = tree_hash(workdir)
        source_path = workdir / "service.js"
        apply_started = time.perf_counter()
        if arm == "raw_healing":
            generated_text = raw_repair_source(task, source_path.read_text(encoding="utf-8"))
            source_path.write_text(generated_text, encoding="utf-8")
            applicable = True
        else:
            patch = aether_repair_patch(task)
            generated_text = json.dumps(patch, sort_keys=True)
            result = base.apply_aether_patch(workdir, patch)
            applicable = bool(result["ok"])
            validation_failed = bool(result["validation_failed"])
            rollback_triggered = bool(result["rolled_back"])
            if not applicable:
                error_type = "validation_failed" if validation_failed else "aether_apply_failed"
                error_detail = "; ".join(base.stringify_error(item) for item in result["errors"])
        application_time_ms = base.elapsed_ms(apply_started)

        syntax_valid, syntax_detail = base.check_syntax(source_path, "javascript")
        if not syntax_valid:
            error_type = error_type or "syntax_error"
            error_detail = error_detail or syntax_detail
        verify_started = time.perf_counter()
        verification = run_probe(workdir, task)
        verification_time_ms = base.elapsed_ms(verify_started)
        hidden_test_pass = verification.returncode == 0 and task.expected_stdout in verification.stdout
        if not hidden_test_pass:
            error_type = error_type or "hidden_test_failed"
            error_detail = error_detail or verification.stderr.strip() or verification.stdout.strip()
            if arm == "aether_healing" and applicable:
                restore_from_snapshot(workdir, before_hash)
                rollback_triggered = True

        after_hash = tree_hash(workdir)
        repository_corrupted = after_hash != before_hash and not healing_success(task, hidden_test_pass, syntax_valid)
    except Exception as exc:
        error_type = error_type or type(exc).__name__
        error_detail = error_detail or str(exc)
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)

    safety_success = task.invalid_repair and not repository_corrupted and (
        validation_failed or rollback_triggered or not applicable
    )
    repair_success = not task.invalid_repair and syntax_valid and hidden_test_pass
    return {
        "task_id": task.task_id,
        "pair_id": f"{task.task_id}:trial-{trial}",
        "trial": trial,
        "arm": arm,
        "language": "javascript",
        "failure_type": task.failure_type,
        "invalid_repair": task.invalid_repair,
        "repair_success": repair_success,
        "safety_success": safety_success,
        "outcome_success": repair_success or safety_success,
        "applicable": applicable,
        "validation_failed": validation_failed,
        "rollback_triggered": rollback_triggered,
        "syntax_valid": syntax_valid,
        "hidden_test_pass": hidden_test_pass,
        "repository_corrupted_on_failure": repository_corrupted,
        "output_tokens": base.count_tokens(generated_text),
        "output_bytes": len(generated_text.encode("utf-8")),
        "token_estimator": base.token_estimator_name(),
        "application_time_ms": round(application_time_ms, 6) if application_time_ms is not None else None,
        "verification_time_ms": round(verification_time_ms, 6) if verification_time_ms is not None else None,
        "total_task_time_ms": round(base.elapsed_ms(started), 6),
        "artifact_sha256": hash_text(generated_text) if generated_text else None,
        "error_type": error_type,
        "error_detail": error_detail,
    }


def healing_success(task: HealingTask, hidden_test_pass: bool, syntax_valid: bool) -> bool:
    return not task.invalid_repair and hidden_test_pass and syntax_valid


def write_project(workdir: Path, task: HealingTask) -> None:
    (workdir / "service.js").write_text(service_source(), encoding="utf-8")
    (workdir / "probe.js").write_text(probe_source(task), encoding="utf-8")


def service_source() -> str:
    helpers = "\n".join(
        f"function helper{i}(value) {{ return value + {i}; }}" for i in range(1, 38)
    )
    return (
        "const allowedTelemetryKeys = ['temperature_c', 'humidity_pct'];\n\n"
        "function validateTelemetry(payload) {\n"
        "  const keys = Object.keys(payload);\n"
        "  return keys.every(key => allowedTelemetryKeys.includes(key));\n"
        "}\n\n"
        "function formatDisplayName(profile) {\n"
        "  return profile.displayName.trim().toLowerCase();\n"
        "}\n\n"
        "function normalizeScore(reading) {\n"
        "  return Math.round(reading.value);\n"
        "}\n\n"
        "function parsePayload(text) {\n"
        "  return JSON.parse(text);\n"
        "}\n\n"
        "function slowFib(n) {\n"
        "  if (n <= 1) return n;\n"
        "  return slowFib(n - 1) + slowFib(n - 2);\n"
        "}\n\n"
        "const fibCache = new Map([[0, 0], [1, 1]]);\n\n"
        "function memoFib(n) {\n"
        "  if (fibCache.has(n)) return fibCache.get(n);\n"
        "  const value = memoFib(n - 1) + memoFib(n - 2);\n"
        "  fibCache.set(n, value);\n"
        "  return value;\n"
        "}\n\n"
        "function calculateForecast(seed) {\n"
        "  return slowFib(seed);\n"
        "}\n\n"
        f"{helpers}\n\n"
        "module.exports = { validateTelemetry, formatDisplayName, normalizeScore, parsePayload, calculateForecast };\n"
    )


def probe_source(task: HealingTask) -> str:
    if task.task_id in {"sh-js-schema-key", "sh-js-invalid-rollback"}:
        payload = "{temperature_c: 21, humidity_pct: 45, vibration_hz: 12}"
        return (
            "const { validateTelemetry } = require('./service.js');\n"
            f"console.log(`accepted=${{validateTelemetry({payload})}}`);\n"
        )
    if task.task_id in {"sh-js-null-user", "sh-js-behavior-break"}:
        return (
        "const { formatDisplayName } = require('./service.js');\n"
        "console.log(`display=${formatDisplayName({displayName: null})}`);\n"
        )
    if task.task_id == "sh-js-negative-clamp":
        return (
            "const { normalizeScore } = require('./service.js');\n"
            "console.log(`score=${normalizeScore({value: -7})}`);\n"
        )
    if task.task_id == "sh-js-json-parse":
        return (
            "const { parsePayload } = require('./service.js');\n"
            "console.log(`keys=${Object.keys(parsePayload('{bad json')).length}`);\n"
        )
    if task.task_id == "sh-js-cache-fast-path":
        return (
            "const { calculateForecast } = require('./service.js');\n"
            "console.log(`fib=${calculateForecast(10)}`);\n"
        )
    if task.task_id == "sh-js-path-traversal":
        payload = "{temperature_c: 21, humidity_pct: 45, vibration_hz: 12}"
        return (
            "const { validateTelemetry } = require('./service.js');\n"
            f"console.log(`accepted=${{validateTelemetry({payload})}}`);\n"
        )
    raise ValueError(f"Unknown healing task: {task.task_id}")


def raw_repair_source(task: HealingTask, source: str) -> str:
    if task.task_id == "sh-js-schema-key":
        return source.replace(
            "const allowedTelemetryKeys = ['temperature_c', 'humidity_pct'];",
            "const allowedTelemetryKeys = ['temperature_c', 'humidity_pct', 'vibration_hz'];",
        )
    if task.task_id == "sh-js-null-user":
        return source.replace(
            "function formatDisplayName(profile) {\n  return profile.displayName.trim().toLowerCase();\n}",
            "function formatDisplayName(profile) {\n  return (profile.displayName || 'anonymous').trim().toLowerCase();\n}",
        )
    if task.task_id == "sh-js-negative-clamp":
        return source.replace(
            "function normalizeScore(reading) {\n  return Math.round(reading.value);\n}",
            "function normalizeScore(reading) {\n  return Math.max(0, Math.round(reading.value));\n}",
        )
    if task.task_id == "sh-js-json-parse":
        return source.replace(
            "function parsePayload(text) {\n  return JSON.parse(text);\n}",
            "function parsePayload(text) {\n  try {\n    return JSON.parse(text);\n  } catch {\n    return {};\n  }\n}",
        )
    if task.task_id == "sh-js-cache-fast-path":
        return source.replace(
            "function calculateForecast(seed) {\n  return slowFib(seed);\n}",
            "function calculateForecast(seed) {\n  return memoFib(seed);\n}",
        )
    if task.task_id == "sh-js-invalid-rollback":
        return source.replace(
            "function validateTelemetry(payload) {\n  const keys = Object.keys(payload);\n  return keys.every(key => allowedTelemetryKeys.includes(key));\n}",
            "function validateTelemetry(payload) {\n  return (;\n}",
        )
    if task.task_id == "sh-js-behavior-break":
        return source.replace(
            "function formatDisplayName(profile) {\n  return profile.displayName.trim().toLowerCase();\n}",
            "function formatDisplayName(profile) {\n  return 'guest';\n}",
        )
    if task.task_id == "sh-js-path-traversal":
        return source.replace(
            "function validateTelemetry(payload) {\n  const keys = Object.keys(payload);\n  return keys.every(key => allowedTelemetryKeys.includes(key));\n}",
            "function validateTelemetry(payload) {\n  return (;\n}",
        )
    raise ValueError(f"Unknown healing task: {task.task_id}")


def aether_repair_patch(task: HealingTask) -> dict[str, Any]:
    if task.task_id == "sh-js-schema-key":
        return patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": (
                    "const keys = Object.keys(payload);\n"
                    "return keys.every(key => allowedTelemetryKeys.includes(key) || key === 'vibration_hz');"
                ),
            },
            symbol="validateTelemetry",
        )
    if task.task_id == "sh-js-null-user":
        return patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": "return (profile.displayName || 'anonymous').trim().toLowerCase();",
            },
            symbol="formatDisplayName",
        )
    if task.task_id == "sh-js-negative-clamp":
        return patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": "return Math.max(0, Math.round(reading.value));",
            },
            symbol="normalizeScore",
        )
    if task.task_id == "sh-js-json-parse":
        return patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": "try {\n  return JSON.parse(text);\n} catch {\n  return {};\n}",
            },
            symbol="parsePayload",
        )
    if task.task_id == "sh-js-cache-fast-path":
        return patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": "return memoFib(seed);",
            },
            symbol="calculateForecast",
        )
    if task.task_id == "sh-js-invalid-rollback":
        value = patch(
            "update_import",
            {"operation": "add_import", "imports": ["const fs = require('fs');"]},
        )
        value["target"]["file"] = "../service.js"
        return value
    if task.task_id == "sh-js-behavior-break":
        return patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": "return 'guest';",
            },
            symbol="formatDisplayName",
        )
    if task.task_id == "sh-js-path-traversal":
        value = patch(
            "modify_function",
            {
                "operation": "replace_body",
                "payload": "return true;",
            },
            symbol="validateTelemetry",
        )
        value["target"]["file"] = "../service.js"
        return value
    raise ValueError(f"Unknown healing task: {task.task_id}")


def patch(action: str, changes: dict[str, Any], *, symbol: str | None = None) -> dict[str, Any]:
    target = {"file": "service.js"}
    if symbol is not None:
        target.update({"symbol": symbol, "symbol_type": "function"})
    return {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": action,
        "target": target,
        "changes": changes,
        "metadata": {
            "agent_id": "self-healing-benchmark",
            "model": "deterministic-healing-agent",
        },
    }


def run_probe(workdir: Path, task: HealingTask) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "probe.js"],
        cwd=str(workdir),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def baseline_original_failures() -> list[str]:
    failures = []
    for task in TASKS:
        workdir = Path(tempfile.mkdtemp(prefix=f"aether-healing-baseline-{task.task_id}-"))
        try:
            write_project(workdir, task)
            result = run_probe(workdir, task)
            if result.returncode != 0 or task.expected_stdout not in result.stdout:
                failures.append(task.task_id)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return failures


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm = {}
    for arm in ARMS:
        selected = [item for item in records if item["arm"] == arm]
        valid = [item for item in selected if not item["invalid_repair"]]
        invalid = [item for item in selected if item["invalid_repair"]]
        by_arm[arm] = {
            "records": len(selected),
            "repair_successes": sum(item["repair_success"] for item in valid),
            "repair_success_rate": rate(valid, "repair_success"),
            "safety_successes": sum(item["safety_success"] for item in invalid),
            "safety_success_rate": rate(invalid, "safety_success"),
            "corruptions": sum(item["repository_corrupted_on_failure"] for item in selected),
            "output_tokens": sum(int(item["output_tokens"]) for item in selected),
            "output_bytes": sum(int(item["output_bytes"]) for item in selected),
            "mean_total_task_time_ms": round(
                sum(float(item["total_task_time_ms"]) for item in selected) / len(selected),
                6,
            ),
        }
    raw = by_arm["raw_healing"]
    aether = by_arm["aether_healing"]
    return {
        "records": len(records),
        "pairs": len({item["pair_id"] for item in records}),
        "by_arm": by_arm,
        "aether_output_token_savings_pct": pct(raw["output_tokens"], aether["output_tokens"]),
        "aether_output_byte_savings_pct": pct(raw["output_bytes"], aether["output_bytes"]),
        "aether_repair_success_delta_percentage_points": round(
            (aether["repair_success_rate"] - raw["repair_success_rate"]) * 100,
            6,
        ),
        "aether_corruption_reduction": raw["corruptions"] - aether["corruptions"],
    }


def rate(records: list[dict[str, Any]], field: str) -> float:
    return round(sum(bool(item[field]) for item in records) / len(records), 6) if records else 0.0


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = list(records[0]) if records else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def tree_hash(path: Path) -> str:
    return base.tree_hash(path)


def restore_from_snapshot(workdir: Path, before_hash: str) -> None:
    # The deterministic benchmark project is generated from source, so restore it
    # by recreating the project files and verifying the original tree hash.
    service = workdir / "service.js"
    service.write_text(service_source(), encoding="utf-8")
    if tree_hash(workdir) != before_hash:
        raise RuntimeError("controller rollback failed to restore pre-repair state")


def pct(left: float, right: float) -> float | None:
    if left == 0:
        return None
    return round((left - right) / left * 100, 6)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
