"""
Phase 1 test suite — Validation Layer
Tests every valid action type, every invalid scenario, and security rule enforcement.

Target: 100% schema-invalid patch rejection (FR-002/FR-003 from SRS).

Run with:
    cd sdk/python
    pip install jsonschema pytest
    pytest tests/test_validation.py -v
"""

import json
import uuid
from datetime import datetime, timezone

import pytest

from ai_runtime import PatchEngine
from ai_runtime.validation import validate_schema, validate_schema_from_string


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_patch(**overrides) -> dict:
    """
    Build a minimal valid patch. Top-level keys in overrides fully replace
    defaults (shallow merge), so 'target' and 'changes' are not contaminated
    by default symbol fields when building run_script/replace_block patches.
    """
    base = {
        "schema_version": "1.0",
        "patch_id": str(uuid.uuid4()),
        "action": "modify_function",
        "target": {
            "file": "src/app.py",
            "symbol": "calculate_total",
            "symbol_type": "function",
        },
        "changes": {
            "operation": "replace_body",
            "payload": "    return sum(items)",
        },
    }
    base.update(overrides)  # shallow: target/changes fully replaced if provided
    return base


@pytest.fixture
def engine() -> PatchEngine:
    return PatchEngine()


# ---------------------------------------------------------------------------
# VALID patches — all 7 action types
# ---------------------------------------------------------------------------

class TestValidPatches:
    def test_modify_function(self, engine):
        patch = make_patch(action="modify_function", changes={"operation": "replace_body", "payload": "    pass"})
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_add_function(self, engine):
        patch = make_patch(
            action="add_function",
            target={"file": "src/utils.py", "symbol": "new_helper", "symbol_type": "function"},
            changes={"operation": "replace_body", "payload": "def new_helper():\n    pass"},
        )
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_remove_function(self, engine):
        patch = make_patch(
            action="remove_function",
            target={"file": "src/utils.py", "symbol": "old_func", "symbol_type": "function"},
            changes={"operation": "replace_body"},
        )
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_modify_class(self, engine):
        patch = make_patch(
            action="modify_class",
            target={"file": "src/models.py", "symbol": "User", "symbol_type": "class"},
            changes={"operation": "insert_after", "payload": "    role: str = 'user'"},
        )
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_update_import(self, engine):
        patch = make_patch(
            action="update_import",
            target={"file": "src/app.py"},
            changes={"operation": "add_import", "imports": ["from pathlib import Path"]},
        )
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_replace_block(self, engine):
        patch = make_patch(
            action="replace_block",
            target={"file": "src/app.py"},
            changes={
                "operation": "context_replace",
                "context_before": "# old block start",
                "payload": "# new block content",
            },
        )
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_run_script_elevated(self, engine):
        patch = make_patch(
            action="run_script",
            target={"file": "scripts/migrate.py"},
            changes={"operation": "run", "payload": "print('migration')"},
        )
        report = engine.validate(patch, trust_level="elevated")
        assert report.ok, report.first_error

    def test_patch_with_constraints(self, engine):
        patch = make_patch(constraints={
            "timeout_ms": 3000,
            "memory_limit_mb": 64,
            "allow_network": False,
            "sandbox_tier": "t3_subprocess",
        })
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_patch_with_metadata(self, engine):
        patch = make_patch(metadata={
            "agent_id": "agent-001",
            "model": "gemini-2.5-pro",
            "intent": "Refactor calculateTotal to use sum()",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        report = engine.validate(patch)
        assert report.ok, report.first_error

    def test_validation_elapsed_ms_under_20(self, engine):
        """Validate 20ms performance SLO."""
        patch = make_patch()
        report = engine.validate(patch)
        assert report.ok
        assert report.elapsed_ms < 20, f"Validation took {report.elapsed_ms}ms, SLO is <20ms"


# ---------------------------------------------------------------------------
# INVALID patches — schema violations (Gate 1)
# ---------------------------------------------------------------------------

class TestSchemaRejection:
    def test_missing_schema_version(self, engine):
        patch = make_patch()
        del patch["schema_version"]
        report = engine.validate(patch)
        assert not report.ok
        assert "schema_version" in report.first_error

    def test_wrong_schema_version(self, engine):
        patch = make_patch(schema_version="2.0")
        report = engine.validate(patch)
        assert not report.ok

    def test_missing_patch_id(self, engine):
        patch = make_patch()
        del patch["patch_id"]
        report = engine.validate(patch)
        assert not report.ok

    def test_invalid_patch_id_format(self, engine):
        patch = make_patch(patch_id="not-a-uuid")
        report = engine.validate(patch)
        assert not report.ok

    def test_unknown_action(self, engine):
        patch = make_patch(action="delete_database")
        report = engine.validate(patch)
        assert not report.ok

    def test_missing_target_file(self, engine):
        patch = make_patch()
        del patch["target"]["file"]
        report = engine.validate(patch)
        assert not report.ok

    def test_unknown_operation(self, engine):
        patch = make_patch(changes={"operation": "destroy_everything"})
        report = engine.validate(patch)
        assert not report.ok

    def test_extra_top_level_field_rejected(self, engine):
        """additionalProperties: false — no unexpected fields allowed."""
        patch = make_patch()
        patch["injected_field"] = "malicious"
        report = engine.validate(patch)
        assert not report.ok

    def test_payload_too_large(self, engine):
        """Payloads > 64KB should be rejected."""
        patch = make_patch(changes={"operation": "replace_body", "payload": "x" * 70000})
        report = engine.validate(patch)
        assert not report.ok

    def test_timeout_below_minimum(self, engine):
        patch = make_patch(constraints={"timeout_ms": 5})
        report = engine.validate(patch)
        assert not report.ok

    def test_timeout_above_maximum(self, engine):
        patch = make_patch(constraints={"timeout_ms": 999999})
        report = engine.validate(patch)
        assert not report.ok

    def test_malformed_json_string(self, engine):
        report = engine.validate("{ this is not json }")
        assert not report.ok
        assert "Invalid JSON" in report.first_error

    def test_empty_dict(self, engine):
        report = engine.validate({})
        assert not report.ok

    def test_null_input(self, engine):
        from ai_runtime.validation import validate_schema
        result = validate_schema(None)  # type: ignore
        assert not result.valid


# ---------------------------------------------------------------------------
# Security rule enforcement (Gate 2)
# ---------------------------------------------------------------------------

class TestSecurityRules:
    def test_absolute_path_rejected(self, engine):
        patch = make_patch(target={"file": "/etc/passwd", "symbol": "x", "symbol_type": "function"})
        report = engine.validate(patch)
        assert not report.ok
        assert not report.ok  # path caught by schema pattern or rules path check

    def test_path_traversal_rejected(self, engine):
        patch = make_patch(target={"file": "../../secrets.env", "symbol": "x", "symbol_type": "function"})
        report = engine.validate(patch)
        assert not report.ok
        assert not report.ok and any("traversal" in e.lower() or "does not match" in e for e in report.errors)

    def test_os_system_in_payload_rejected(self, engine):
        patch = make_patch(changes={
            "operation": "replace_body",
            "payload": "import os; os.system('rm -rf /')",
        })
        report = engine.validate(patch)
        assert not report.ok
        assert not report.ok and bool(report.errors)

    def test_subprocess_in_payload_rejected(self, engine):
        patch = make_patch(changes={
            "operation": "replace_body",
            "payload": "import subprocess; subprocess.run(['ls'])",
        })
        report = engine.validate(patch)
        assert not report.ok

    def test_eval_in_payload_rejected(self, engine):
        patch = make_patch(changes={
            "operation": "replace_body",
            "payload": "eval('__import__(\"os\").system(\"id\")')",
        })
        report = engine.validate(patch)
        assert not report.ok

    def test_run_script_without_elevation_rejected(self, engine):
        patch = make_patch(
            action="run_script",
            target={"file": "scripts/run.py"},
            changes={"operation": "run", "payload": "print('hello')"},
        )
        report = engine.validate(patch)  # standard trust_level by default
        assert not report.ok
        assert not report.ok and any("elevated" in e or "trust_level" in e for e in report.errors)

    def test_disallowed_operation_for_action(self, engine):
        """modify_function does not allow 'run' operation."""
        patch = make_patch(changes={"operation": "run", "payload": "pass"})
        report = engine.validate(patch)
        assert not report.ok

    def test_windows_absolute_path_rejected(self, engine):
        patch = make_patch(target={"file": "C:\\Windows\\System32\\cmd.exe", "symbol": "x", "symbol_type": "function"})
        report = engine.validate(patch)
        assert not report.ok


# ---------------------------------------------------------------------------
# PatchEngine state tracking
# ---------------------------------------------------------------------------

class TestEngineStats:
    def test_stats_track_applied_and_rejected(self, engine):
        valid   = make_patch()
        invalid = make_patch(action="evil_action")

        engine.validate(valid)     # would apply but process() needed
        engine.validate(invalid)   # rejected

        assert engine.stats["rejected"] == 1

    def test_process_increments_applied(self, tmp_path):
        from ai_runtime import PatchEngine
        engine = PatchEngine(project_root=str(tmp_path))
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        app_py = src_dir / "app.py"
        app_py.write_text("def calculate_total(items):\n    return 0\n")
        
        patch = make_patch()
        engine.process(patch)
        assert engine.stats["applied"] == 1
        assert engine.stats["total"]   == 1
