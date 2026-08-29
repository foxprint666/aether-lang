from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

from ai_runtime.cli import main


def _patch(project: Path) -> dict:
    src = project / "src"
    src.mkdir(exist_ok=True)
    (src / "cart.py").write_text(
        "def calculate_total(items):\n    return 0\n",
        encoding="utf-8",
    )
    return {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": "modify_function",
        "target": {
            "file": "src/cart.py",
            "symbol": "calculate_total",
            "symbol_type": "function",
        },
        "changes": {
            "operation": "replace_body",
            "payload": "return sum(items)",
        },
    }


def test_cli_validate_accepts_patch_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps(_patch(tmp_path)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "validate", str(patch_path), "--json"])

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["errors"] == []


def test_cli_apply_snapshots_and_modifies_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps(_patch(tmp_path)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "apply", str(patch_path), "--json"])

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["snapshot_id"]
    assert "return sum(items)" in (tmp_path / "src" / "cart.py").read_text(encoding="utf-8")


def test_cli_rollback_restores_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps(_patch(tmp_path)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "apply", str(patch_path), "--json"])
    assert main() == 0
    snapshot_id = json.loads(capsys.readouterr().out)["snapshot_id"]

    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "rollback", snapshot_id])
    assert main() == 0
    assert "return 0" in (tmp_path / "src" / "cart.py").read_text(encoding="utf-8")


def test_cli_snapshots_lists_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps(_patch(tmp_path)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "apply", str(patch_path), "--json"])
    assert main() == 0
    snapshot_id = json.loads(capsys.readouterr().out)["snapshot_id"]

    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "snapshots"])
    assert main() == 0
    output = capsys.readouterr().out
    assert snapshot_id in output
    assert "committed" in output


def test_cli_validate_rejects_invalid_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    patch_path = tmp_path / "bad.json"
    patch_path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["aether", "--project", str(tmp_path), "validate", str(patch_path)])

    assert main() == 1
    assert "REJECTED:" in capsys.readouterr().err


def test_cli_validate_serializes_rule_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patch_path = tmp_path / "bad-rule.json"
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "patch_id": "22222222-2222-4222-8222-222222222222",
                "action": "add_function",
                "target": {
                    "file": "demo.py",
                    "symbol": "demo",
                    "symbol_type": "function",
                },
                "changes": {
                    "operation": "insert_after",
                    "payload": "def demo():\n    return 1\n",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["aether", "--project", str(tmp_path), "validate", str(patch_path), "--json"],
    )

    assert main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "operation_allow_list" in payload["errors"][0]
