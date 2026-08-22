from __future__ import annotations

import sys
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
AGENTS = Path(__file__).resolve().parents[1] / "agents"
sys.path.insert(0, str(AGENTS))
sys.path.insert(0, str(BENCHMARKS))

from paired_blind_protocol import (  # noqa: E402
    FULL_FILE_ARM,
    PATCH_ARM,
    arm_descriptor,
    build_prompt_core,
    canonical_hash,
)
from run_paired_agent import load_generations, make_task  # noqa: E402


def test_prompt_core_is_allowlisted_and_shared() -> None:
    tasks = [{
        "task_id": "opaque", "language": "python", "source_id": "one",
        "source_file": "a.py", "description": "change it", "test_command": "secret",
    }]
    core = build_prompt_core(tasks, {"one": "def f():\n    pass\n"})
    assert "test_command" not in str(core)
    digest = canonical_hash(core)
    assert arm_descriptor(digest, PATCH_ARM, 1)["prompt_core_sha256"] == digest
    assert arm_descriptor(digest, FULL_FILE_ARM, 1)["prompt_core_sha256"] == digest


def test_arm_descriptors_are_distinct_and_deterministic() -> None:
    patch = arm_descriptor("a" * 64, PATCH_ARM, 2)
    full = arm_descriptor("a" * 64, FULL_FILE_ARM, 2)
    assert canonical_hash(patch) == canonical_hash(dict(patch))
    assert canonical_hash(patch) != canonical_hash(full)


def test_paired_tasks_use_hidden_test_command_execution() -> None:
    task = make_task({
        "task_id": "opaque",
        "language": "python",
        "repository": "example",
        "repository_manifest": "benchmarks/repositories/example.json",
        "fixture": "repo:git:example",
        "source_file": "src/example.py",
        "description": "change it",
        "test_command": "python -c \"raise SystemExit(1)\"",
    })

    assert task.category == "external_agent_patch"
    assert task.test_command == "python -c \"raise SystemExit(1)\""


def test_generation_loader_rejects_incomplete_matched_pairs(tmp_path: Path) -> None:
    task = make_task({
        "task_id": "opaque",
        "language": "python",
        "repository": "example",
        "repository_manifest": "benchmarks/repositories/example.json",
        "fixture": "repo:git:example",
        "source_file": "src/example.py",
        "description": "change it",
        "test_command": "python -c \"raise SystemExit(1)\"",
    })
    (tmp_path / "patch-trial-1.json").write_text(
        '{"trial": 1, "arm": "aether_patch", "records": [{"task": "opaque", "patch": {}}]}',
        encoding="utf-8",
    )

    try:
        load_generations(tmp_path, {"opaque": task})
    except ValueError as exc:
        assert "coverage mismatch" in str(exc)
    else:
        raise AssertionError("incomplete paired generations should be rejected")
