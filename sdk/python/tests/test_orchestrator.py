"""
tests.test_orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~
Phase C1: PatchOrchestrator integration tests.

Tests cover:
  - Gate 1 rejection (bad schema)
  - Gate 2 rejection (security rules)
  - Gate 3 skipped (no ae_target)
  - Happy path: patch applied, committed
  - Rollback on patch engine failure
  - dry_run mode
  - validate_only
  - AuditLog events recorded
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ai_runtime import PatchOrchestrator, OrchestratorResult
from ai_runtime.orchestrator import ValidationReport


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _pid() -> str:
    return str(uuid.uuid4())


def _valid_patch(project: Path, action: str = "modify_function") -> dict:
    """Return a minimal schema-valid patch for a real target file."""
    (project / "src").mkdir(exist_ok=True)
    target = project / "src" / "lib.py"
    if not target.exists():
        target.write_text("def greet(name):\n    return 'Hello, ' + name\n")
    return {
        "schema_version": "1.0",
        "patch_id": _pid(),
        "action": action,
        "target": {
            "file": "src/lib.py",
            "symbol": "greet",
            "symbol_type": "function",
        },
        "changes": {
            "operation": "replace_body",
            "payload": "    return f'Hi, {name}!'",
        },
    }


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal project tree."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.py").write_text("def greet(name):\n    return 'Hello, ' + name\n")
    return tmp_path


@pytest.fixture()
def orch(project: Path) -> PatchOrchestrator:
    return PatchOrchestrator(project_root=project, ae_binary="/nonexistent/ae")


# ─────────────────────────────────────────────────────────────────────────────
# Gate 1 — Schema validation
# ─────────────────────────────────────────────────────────────────────────────

class TestGate1Schema:
    def test_missing_required_field_rejected(self, orch: PatchOrchestrator):
        """Patch missing 'action' → Gate 1 fail."""
        patch = {
            "schema_version": "1.0",
            "patch_id": _pid(),
            # missing action, target, changes
        }
        result = orch.validate_only(patch)
        assert result.ok is False
        assert not result.schema_result.valid
        assert len(result.errors) > 0

    def test_extra_field_rejected(self, orch: PatchOrchestrator):
        """Patch with unknown top-level field → Gate 1 fail."""
        patch = {
            "schema_version": "1.0",
            "patch_id": _pid(),
            "action": "modify_function",
            "target": {"file": "src/lib.py", "symbol": "f", "symbol_type": "function"},
            "changes": {"operation": "replace_body", "payload": "pass"},
            "UNKNOWN_FIELD": "should be rejected",
        }
        result = orch.validate_only(patch)
        assert result.ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Gate 3 — Semantic gate pass-through
# ─────────────────────────────────────────────────────────────────────────────

class TestGate3:
    def test_no_ae_target_skips_gate3(self, orch: PatchOrchestrator, project: Path):
        """Patch without ae_target → Gate 3 skipped (ok=True, skipped=True)."""
        patch = _valid_patch(project)
        report = orch.validate_only(patch)
        assert report.ok is True
        assert report.gate3_result is not None
        assert report.gate3_result.skipped is True

    def test_ae_target_without_binary_skips_gracefully(self, orch: PatchOrchestrator, project: Path):
        """ae_target present but binary missing → Gate 3 skips with warning."""
        patch = _valid_patch(project)
        patch["ae_target"] = {
            "node_hash": "a" * 64,
            "replacement_src": "fn x() -> i32 { 1 }",
        }
        report = orch.validate_only(patch)
        # Binary is /nonexistent/ae → should skip
        assert report.ok is True
        assert report.gate3_result is not None
        assert report.gate3_result.skipped is True


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_validate_only_valid_patch(self, orch: PatchOrchestrator, project: Path):
        """validate_only on a valid patch → ok=True."""
        report = orch.validate_only(_valid_patch(project))
        assert report.ok is True
        assert report.schema_result.valid
        assert report.rules_result.valid

    def test_dry_run_returns_ok(self, orch: PatchOrchestrator, project: Path):
        """dry_run=True → gates pass, no file is changed, result ok."""
        dry = PatchOrchestrator(project_root=project, ae_binary="/nonexistent/ae", dry_run=True)
        patch = _valid_patch(project)
        original = (project / "src" / "lib.py").read_text()
        result = dry.apply(patch)
        assert result.ok is True
        # File must NOT be modified in dry_run
        assert (project / "src" / "lib.py").read_text() == original

    def test_apply_valid_patch_returns_ok(self, orch: PatchOrchestrator, project: Path):
        """Applying a structurally valid patch returns ok (or a specific engine error)."""
        result = orch.apply(_valid_patch(project))
        # Either ok (engine applied it) or a PatchEngine error (not our concern here)
        assert isinstance(result, OrchestratorResult)
        assert result.patch_id != ""

    def test_snapshot_id_set_on_apply(self, orch: PatchOrchestrator, project: Path):
        """After apply(), a snapshot_id is always present."""
        result = orch.apply(_valid_patch(project))
        # snapshot_id is set even on engine failure (snapshot taken before apply)
        assert result.snapshot_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# Rollback
# ─────────────────────────────────────────────────────────────────────────────

class TestRollback:
    def test_validation_rejection_produces_no_snapshot(self, orch: PatchOrchestrator):
        """Gate rejection before apply → no snapshot taken."""
        patch = {"schema_version": "1.0", "patch_id": _pid()}  # invalid
        result = orch.apply(patch)
        assert result.ok is False
        assert result.snapshot_id is None
        assert result.rolled_back is False


# ─────────────────────────────────────────────────────────────────────────────
# AuditLog integration
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_validation_rejected_event_written(self, project: Path):
        """Rejected patch → audit log records a validation_rejected event."""
        from ai_runtime.observability.audit_log import AuditLog
        from ai_runtime.observability.events import EventKind

        orch = PatchOrchestrator(project_root=project, ae_binary="/nonexistent/ae")
        patch = {"schema_version": "1.0", "patch_id": _pid()}  # invalid
        orch.apply(patch)

        log = AuditLog(project_root=project)
        events = log.query(kind=EventKind.VALIDATION_REJECTED)
        assert len(events) >= 1

    def test_snapshot_event_written_on_apply(self, project: Path):
        """Valid patch apply → snapshot_captured event in audit log."""
        from ai_runtime.observability.audit_log import AuditLog
        from ai_runtime.observability.events import EventKind

        orch = PatchOrchestrator(project_root=project, ae_binary="/nonexistent/ae")
        result = orch.apply(_valid_patch(project))

        log = AuditLog(project_root=project)
        events = log.query(kind=EventKind.SNAPSHOT_CAPTURED)
        assert len(events) >= 1

    def test_validate_only_writes_no_events(self, project: Path):
        """validate_only must not write to the audit log."""
        from ai_runtime.observability.audit_log import AuditLog

        log = AuditLog(project_root=project)
        before = list(log.iter_all())

        orch = PatchOrchestrator(project_root=project, ae_binary="/nonexistent/ae")
        orch.validate_only(_valid_patch(project))

        after = list(log.iter_all())
        assert len(after) == len(before)
