from __future__ import annotations

import json
from pathlib import Path

import pytest


BENCHMARKS = Path(__file__).resolve().parents[1]
AGENTS = BENCHMARKS / "agents"

import sys

sys.path.insert(0, str(AGENTS))

from blind_protocol import build_blind_descriptor, descriptor_sha256  # noqa: E402
from export_blind_packets import assert_blind  # noqa: E402


def descriptor(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "task": "opaque-1",
        "trial": 1,
        "language": "python",
        "repository": "example",
        "fixture": "repo:git:example",
        "source_file": "src/example.py",
        "description": "Change visible behavior.",
        "source": "def value():\n    return 1\n",
    }
    values.update(overrides)
    return build_blind_descriptor(**values)  # type: ignore[arg-type]


def test_descriptor_is_hash_locked_and_contains_no_oracle_fields() -> None:
    value = descriptor()

    assert value["descriptor_sha256"] == descriptor_sha256(value)
    assert_blind(value)
    serialized = json.dumps(value, sort_keys=True)
    assert "test_command" not in serialized
    assert "expected_content" not in serialized
    assert '"patch"' not in serialized


@pytest.mark.parametrize("field", ["trial", "description", "source"])
def test_digest_changes_with_visible_prompt_context(field: str) -> None:
    original = descriptor()
    changed = descriptor(**{field: 2 if field == "trial" else "different"})

    assert original["descriptor_sha256"] != changed["descriptor_sha256"]


def test_digest_ignores_only_its_self_referential_field() -> None:
    value = descriptor()
    value["descriptor_sha256"] = "tampered"

    assert descriptor_sha256(value) != "tampered"
