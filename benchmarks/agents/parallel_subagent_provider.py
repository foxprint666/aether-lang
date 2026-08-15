#!/usr/bin/env python
"""Replay independent parallel Codex subagent patch outputs by trial.

This is agent evidence, not external API telemetry. The patches were produced
by separate subagents from task descriptors/source without manifest reference
patches, then stored in `parallel_subagent_trials.json` for reproducibility.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


DATASET = Path(__file__).with_name("parallel_subagent_trials.json")


def main() -> int:
    started = time.perf_counter()
    descriptor = json.load(sys.stdin)
    task_id = str(descriptor.get("task_id"))
    trial = str(descriptor.get("trial", 1))
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    patch = dataset.get(trial, {}).get(task_id)
    if not isinstance(patch, dict):
        print(f"No parallel subagent patch for trial={trial} task_id={task_id}", file=sys.stderr)
        return 1

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    print(json.dumps({
        "patch": patch,
        "agent": {
            "adapter": "parallel_subagent_provider",
            "raw_provider": "codex-parallel-subagents",
            "subagent_trial": trial,
            "provider_normalized": False,
            "provider_normalization_changes": [],
        },
        "usage": {
            "input_tokens": None,
            "output_tokens": None,
            "tool_calls": 0,
            "latency_ms": latency_ms,
            "cost_usd": None,
            "model": "codex-parallel-subagents",
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
