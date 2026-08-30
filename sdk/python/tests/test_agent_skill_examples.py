from __future__ import annotations

import json
from pathlib import Path

from ai_runtime.validation.schema import validate_schema


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_PATCHES = [
    *sorted((ROOT / "agent_skills" / "aether" / "examples").glob("*_patch.json")),
    ROOT / "examples" / "aether_cli" / "patch.json",
]


def test_agent_skill_patch_examples_match_schema() -> None:
    assert EXAMPLE_PATCHES, "expected Aether patch examples"
    for path in EXAMPLE_PATCHES:
        report = validate_schema(json.loads(path.read_text(encoding="utf-8")))
        assert report.valid, f"{path}: {report.errors}"


def test_packaged_skill_covers_debug_and_review_requests() -> None:
    skill = (ROOT / "sdk" / "python" / "ai_runtime" / "agent_skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "debug this repo using Aether" in skill
    assert "code task and an evaluation task" in skill
    assert "what Aether made easier than raw editing" in skill
