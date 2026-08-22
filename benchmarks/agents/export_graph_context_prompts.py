#!/usr/bin/env python
"""Export graph-scoped prompt packets for independent agent trials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS = ROOT / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

import run as base
from context.graph_context import build_graph_packet
from run_paired_agent import load_sources, make_task


DEFAULT_MANIFEST = ROOT / "benchmarks" / "tasks" / "paired_agent_unseen.json"
DEFAULT_OUTPUT_DIR = ROOT / "benchmarks" / "agents" / "graph_context_prompts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--allow-network-repos", action="store_true")
    args = parser.parse_args()
    base.CURRENT_ARGS = argparse.Namespace(allow_network_repos=args.allow_network_repos)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    task_values = manifest["tasks"]
    tasks = {item["task_id"]: make_task(item) for item in task_values}
    sources = load_sources(tasks, task_values)
    packets = [
        build_graph_packet(item, sources[item["source_id"]])
        for item in task_values
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for trial in range(1, args.trials + 1):
        prompt = {
            "role": "You are an isolated coding agent generating Aether structured patches from graph-scoped context only.",
            "trial": trial,
            "arm": "aether_patch",
            "withheld": ["hidden tests", "reference solutions", "full source outside selected symbols"],
            "contract": {
                "schema_version": "1.0",
                "required": ["schema_version", "patch_id", "action", "target", "changes"],
                "output": (
                    "Return one JSON envelope exactly: "
                    "{\"trial\": N, \"arm\": \"aether_patch\", "
                    "\"records\": [{\"task\": task_id, \"patch\": patch_object}, ...]}"
                ),
                "patch_rules": [
                    "Use fresh UUID4 strings.",
                    "Never put replace_body in action; use action=modify_function for functions and methods, or action=modify_class for classes.",
                    "Always include target.symbol_type copied from the selected symbol kind.",
                    "target.file must equal source_file.",
                    "target.symbol must be the bare selected symbol name.",
                    "changes must be an object like {\"operation\": \"replace_body\", \"payload\": \"...\"}, not a list.",
                    "Do not include unknown top-level patch keys.",
                    "For replace_body, payload is the function/method body text only; do not include a function or method signature.",
                    "If the selected symbol is a generator method, payload may contain yield statements but still omits the method signature.",
                    "Honor every semantic constraint in the description; if it says nonnegative, reject negative values.",
                    "When only a method body can be changed and a new parameter is needed, read it from arguments.",
                    "Preserve indentation style from selected_symbols text.",
                    "Do not invent tests or expected answers.",
                ],
            },
            "tasks": packets,
            "instruction": (
                "For every task, infer the minimal Aether patch from description and selected_symbols. "
                "Return only valid JSON, no markdown."
            ),
        }
        path = args.output_dir / f"trial-{trial}.json"
        path.write_text(json.dumps(prompt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
