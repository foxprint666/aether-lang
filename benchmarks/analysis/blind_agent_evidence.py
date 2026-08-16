#!/usr/bin/env python
"""Summarize hash-locked blind external-agent benchmark evidence."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    records = load_records(args.results)
    report = analyze(records)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(rendered, end="")
    return 0


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError(f"Result file has no records array: {path}")
        records.extend(item for item in values if isinstance(item, dict))
    return [item for item in records if item.get("category") == "external_agent_patch"]


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        config = record.get("configuration") or {}
        groups[(str(record.get("task_id")), int(config.get("trial", 0)))].append(record)

    matched: list[dict[str, Any]] = []
    for (task, trial), values in sorted(groups.items()):
        modes = {str((item.get("configuration") or {}).get("mode")): item for item in values}
        patch_hashes = {item.get("generated_patch_sha256") for item in values}
        descriptor_hashes = {item.get("agent_descriptor_sha256") for item in values}
        matched.append({
            "task": task,
            "trial": trial,
            "modes": sorted(modes),
            "identical_patch_across_modes": len(patch_hashes) == 1 and None not in patch_hashes,
            "identical_descriptor_across_modes": len(descriptor_hashes) == 1 and None not in descriptor_hashes,
            "success": {mode: bool(item.get("task_success")) for mode, item in sorted(modes.items())},
        })

    modes = sorted({str((item.get("configuration") or {}).get("mode")) for item in records})
    by_mode = {mode: mode_summary(records, mode) for mode in modes}
    control_records = [
        item for item in records if (item.get("configuration") or {}).get("mode") == "control"
    ]
    output_tokens = sum_number(control_records, "estimated_output_tokens")
    rewrite_tokens = sum_number(control_records, "estimated_traditional_output_tokens")
    output_bytes = sum_number(control_records, "output_size_bytes")
    rewrite_bytes = sum_number(control_records, "traditional_output_size_bytes")

    return {
        "report_version": "blind-external-agent-v1",
        "evidence": {
            "records": len(records),
            "success_rate": rate(sum(bool(item.get("task_success")) for item in records), len(records)),
            "generation_events": len(groups),
            "tasks": sorted({str(item.get("task_id")) for item in records}),
            "task_count": len({str(item.get("task_id")) for item in records}),
            "repositories": sorted({str(item.get("repository")) for item in records}),
            "repository_count": len({str(item.get("repository")) for item in records}),
            "languages": sorted({str(item.get("language")) for item in records}),
            "trials": sorted({int((item.get("configuration") or {}).get("trial", 0)) for item in records}),
            "blind_records": sum(bool(item.get("agent_prompt_blind")) for item in records),
            "oracle_used_records": sum(
                (item.get("configuration") or {}).get("oracle_used_during_generation") is not False
                for item in records
            ),
        },
        "by_mode": by_mode,
        "matched_generation": {
            "groups": len(matched),
            "identical_patch_groups": sum(item["identical_patch_across_modes"] for item in matched),
            "identical_descriptor_groups": sum(item["identical_descriptor_across_modes"] for item in matched),
            "all_modes_present_groups": sum(
                set(item["modes"]) == {"aether", "control", "hybrid", "state"} for item in matched
            ),
            "details": matched,
        },
        "offline_efficiency": {
            "estimator": sorted({item.get("token_estimator") for item in records if item.get("token_estimator")}),
            "patch_output_tokens": output_tokens,
            "full_file_output_tokens": rewrite_tokens,
            "weighted_output_token_savings_pct": savings(output_tokens, rewrite_tokens),
            "patch_output_bytes": output_bytes,
            "full_file_output_bytes": rewrite_bytes,
            "weighted_output_byte_savings_pct": savings(output_bytes, rewrite_bytes),
        },
        "hybrid_routing": dict(sorted(Counter(
            str(item.get("hybrid_selected_mode"))
            for item in records
            if (item.get("configuration") or {}).get("mode") == "hybrid"
        ).items())),
        "limitations": [
            "Generation used independent Codex subagents with prompt-level packet restrictions, not an OS-enforced filesystem sandbox.",
            "The stored provider replays hash-locked agent outputs; it does not claim live provider token, latency, retry, or cost telemetry.",
            "Control, state, Aether, and hybrid apply the same generated structured patch; this isolates application safety and overhead but is not a full-file-generation agent control arm.",
            "Tasks were unpublished before generation but are revealed with this evidence bundle, so future blind trials require fresh tasks.",
        ],
    }


def mode_summary(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    selected = [item for item in records if (item.get("configuration") or {}).get("mode") == mode]
    times = [float(item["edit_to_verified_time_ms"]) for item in selected if item.get("edit_to_verified_time_ms") is not None]
    return {
        "records": len(selected),
        "success_rate": rate(sum(bool(item.get("task_success")) for item in selected), len(selected)),
        "mean_edit_to_verified_time_ms": round(statistics.mean(times), 6) if times else None,
        "median_edit_to_verified_time_ms": round(statistics.median(times), 6) if times else None,
    }


def sum_number(records: list[dict[str, Any]], key: str) -> float:
    return sum(float(item[key]) for item in records if item.get(key) is not None)


def savings(value: float, baseline: float) -> float | None:
    return round((baseline - value) / baseline * 100, 6) if baseline else None


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    efficiency = report["offline_efficiency"]
    matched = report["matched_generation"]
    lines = [
        "# Blind External-Agent Report",
        "",
        f"- Records: `{evidence['records']}` across `{evidence['generation_events']}` independent generation events.",
        f"- Success rate: `{evidence['success_rate']}`.",
        f"- Coverage: `{evidence['repository_count']}` repositories, `{evidence['task_count']}` tasks, `{', '.join(evidence['languages'])}`.",
        f"- Blind records: `{evidence['blind_records']}`; oracle-used records: `{evidence['oracle_used_records']}`.",
        f"- Hash-matched patches across modes: `{matched['identical_patch_groups']}/{matched['groups']}`.",
        f"- Estimated output-token savings versus full-file output: `{efficiency['weighted_output_token_savings_pct']}%`.",
        f"- Emitted-byte savings versus full-file output: `{efficiency['weighted_output_byte_savings_pct']}%`.",
        "",
        "## Modes",
        "",
    ]
    for mode, values in report["by_mode"].items():
        lines.append(
            f"- `{mode}`: `{values['success_rate']}` success, "
            f"`{values['mean_edit_to_verified_time_ms']} ms` mean edit-to-verified."
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
