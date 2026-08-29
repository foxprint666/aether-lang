"""
ai_runtime.orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~
Phase C1: PatchOrchestrator — the single entry point for AI-driven patch
application with full safety guarantees.

Architecture (per-patch lifecycle):

  PatchOrchestrator.apply(patch)
        │
        ├─ Gate 1: Schema validation      (PatchSchemaValidator)
        ├─ Gate 2: Security rules         (RulesEngine)
        ├─ Gate 3: Semantic bridge        (SemanticGate / ae check --json)
        │
        ├─ Pre-apply snapshot             (SnapshotStore.capture)
        │
        ├─ PatchEngine.apply              (safe libcst AST patch)
        │
        ├─ Verdict = OK → commit snapshot
        │            ERR → restore snapshot (incl. deleting new files)
        │
        └─ Emit AuditEvent to AuditLog
           Return OrchestratorResult

All steps are synchronous (no asyncio). Suitable for embedded CLI usage,
test suites, and simple HTTP wrappers.

Thread safety:
  - SnapshotStore uses an advisory file lock → concurrent callers serialize.
  - The orchestrator itself holds no mutable shared state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .validation.rules import check_rules, RulesResult
from .validation.schema import validate_schema, ValidationResult
from .validation.ae_bridge import SemanticGate, BridgeResult
from .snapshot.store import SnapshotStore
from .patch_engine import PatchEngine
from .observability.audit_log import AuditLog
from .observability.events import EventKind, AuditEvent
from ._types import SnapshotHandle, ExecutionResult


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationReport:
    """Combined result of Gates 1–3."""
    ok: bool
    schema_result: ValidationResult
    rules_result: RulesResult
    gate3_result: Optional[BridgeResult] = None
    elapsed_ms: float = 0.0
    patch_id: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class OrchestratorResult:
    """
    Full outcome of one patch-apply attempt.

    Attributes:
        ok:             True if the patch was successfully applied and committed.
        patch_id:       UUID of the patch.
        action:         Patch action string.
        validation:     Gate 1–3 result (always set).
        patch_result:   ExecutionResult (set if run_script was used).
        snapshot_id:    Snapshot UUID (set if snapshot was taken).
        rolled_back:    True if the patch was rolled back.
        elapsed_ms:     Total wall time in ms.
        errors:         Human-readable error messages (empty if ok=True).
    """
    ok: bool
    patch_id: str
    action: str = ""
    validation: Optional[ValidationReport] = None
    patch_result: Optional[ExecutionResult] = None
    snapshot_id: Optional[str] = None
    rolled_back: bool = False
    elapsed_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# PatchOrchestrator
# ─────────────────────────────────────────────────────────────────────────────

class PatchOrchestrator:
    """
    Single-call AI patch orchestrator with layered safety guards.

    Usage::

        orch = PatchOrchestrator(project_root=".")
        result = orch.apply(patch_dict)
        if result.ok:
            print("Patch committed.")
        else:
            print("Rejected:", result.errors)

    Args:
        project_root:    Root of the project being patched.
        store_subdir:    Where to keep snapshots + audit log (default ``.ai_runtime``).
        ae_binary:       Path to the ``ae`` binary for Gate 3. Defaults to
                         PATH lookup; pass ``None`` to disable Gate 3.
        dry_run:         If True, run all gates and snapshot but do NOT actually
                         apply the patch. Useful for CI pre-checks.
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        store_subdir: str = ".ai_runtime",
        ae_binary: Optional[str] = None,
        dry_run: bool = False,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._dry_run = dry_run

        self._gate3 = SemanticGate(ae_binary=ae_binary)
        self._snapshot = SnapshotStore(self.project_root, store_subdir=store_subdir)
        self._engine = PatchEngine(project_root=str(self.project_root))
        self._log = AuditLog(project_root=self.project_root, store_subdir=store_subdir)

    # ── Public API ──────────────────────────────────────────────────────────

    def apply(
        self,
        patch: dict[str, Any],
        *,
        trust_level: str = "standard",
    ) -> OrchestratorResult:
        """
        Apply a patch with full safety guarantees.

        Steps:
            1. Gate 1: JSON schema validation
            2. Gate 2: Security rule checking
            3. Gate 3: ae-sema semantic bridge (if ae_target present)
            4. Snapshot capture (pre-apply)
            5. PatchEngine.apply
            6. Commit or rollback
            7. Emit AuditEvent

        Returns:
            OrchestratorResult
        """
        t0 = time.perf_counter()
        patch_id = patch.get("patch_id", "") if isinstance(patch, dict) else ""
        action = patch.get("action", "") if isinstance(patch, dict) else ""

        # ── Gates 1–3 ──────────────────────────────────────────────────────
        validation = self._run_gates(patch, trust_level=trust_level)

        if not validation.ok:
            elapsed = (time.perf_counter() - t0) * 1000
            self._emit_rejected(patch, validation, elapsed)
            return OrchestratorResult(
                ok=False,
                patch_id=patch_id,
                action=action,
                validation=validation,
                elapsed_ms=elapsed,
                errors=validation.errors,
            )

        # ── Snapshot ───────────────────────────────────────────────────────
        handle: Optional[SnapshotHandle] = None
        if not self._dry_run:
            try:
                handle = self._snapshot.capture(patch_id=patch_id)
                self._log.record(AuditLog.event_snapshot_captured(
                    patch_id=patch_id,
                    snapshot_id=handle.snapshot_id,
                    file_count=handle.file_count,
                    size_bytes=handle.archive_size_bytes,
                ))
            except Exception as exc:
                elapsed = (time.perf_counter() - t0) * 1000
                err = f"Snapshot capture failed: {exc}"
                return OrchestratorResult(
                    ok=False,
                    patch_id=patch_id,
                    action=action,
                    validation=validation,
                    elapsed_ms=elapsed,
                    errors=[err],
                )

        # ── Patch apply ────────────────────────────────────────────────────
        if self._dry_run:
            elapsed = (time.perf_counter() - t0) * 1000
            return OrchestratorResult(
                ok=True,
                patch_id=patch_id,
                action=action,
                validation=validation,
                elapsed_ms=elapsed,
            )

        patch_result: Optional[ExecutionResult] = None
        try:
            res = self._engine.apply(patch)
            if res is not None:
                patch_result = res
        except Exception as exc:
            apply_error = str(exc)
        else:
            if patch_result is not None and not patch_result.success:
                apply_error = patch_result.error or "Unknown sandbox error"
            else:
                apply_error = ""

        elapsed = (time.perf_counter() - t0) * 1000

        if apply_error:
            # Rollback
            if handle:
                try:
                    self._snapshot.restore(handle)
                except Exception as rb_exc:
                    apply_error += f" | Rollback error: {rb_exc}"
            err = apply_error
            self._log.record(AuditLog.event_execution_failed(
                patch_id=patch_id,
                tier="direct",
                elapsed_ms=elapsed,
                error=err,
            ))
            return OrchestratorResult(
                ok=False,
                patch_id=patch_id,
                action=action,
                validation=validation,
                patch_result=patch_result,
                snapshot_id=handle.snapshot_id if handle else None,
                rolled_back=True,
                elapsed_ms=elapsed,
                errors=[err],
            )

        # ── Commit ─────────────────────────────────────────────────────────
        if handle:
            self._snapshot.commit(handle)
            self._log.record(AuditLog.event_committed(
                patch_id=patch_id,
                snapshot_id=handle.snapshot_id,
            ))

        self._log.record(AuditLog.event_execution_ok(
            patch_id=patch_id,
            tier="direct",
            elapsed_ms=elapsed,
        ))

        return OrchestratorResult(
            ok=True,
            patch_id=patch_id,
            action=action,
            validation=validation,
            patch_result=patch_result,
            snapshot_id=handle.snapshot_id if handle else None,
            elapsed_ms=elapsed,
        )

    def validate_only(
        self,
        patch: dict[str, Any],
        *,
        trust_level: str = "standard",
    ) -> ValidationReport:
        """
        Run Gates 1–3 without applying the patch or taking a snapshot.
        Useful for pre-flight checks in CI.
        """
        return self._run_gates(patch, trust_level=trust_level)

    # ── Private helpers ─────────────────────────────────────────────────────

    def _run_gates(
        self,
        patch: dict[str, Any],
        *,
        trust_level: str = "standard",
    ) -> ValidationReport:
        t0 = time.perf_counter()
        patch_id = patch.get("patch_id", "") if isinstance(patch, dict) else ""
        action = patch.get("action", "") if isinstance(patch, dict) else ""
        errors: list[str] = []

        # Gate 1: Schema
        schema_result = validate_schema(patch)
        if not schema_result.valid:
            elapsed = (time.perf_counter() - t0) * 1000
            errors.extend(schema_result.errors)
            return ValidationReport(
                ok=False,
                schema_result=schema_result,
                rules_result=RulesResult(valid=True, violations=[]),
                elapsed_ms=elapsed,
                patch_id=patch_id,
                errors=errors,
            )

        # Gate 2: Security rules
        rules_result = check_rules(patch, trust_level=trust_level)
        if not rules_result.valid:
            elapsed = (time.perf_counter() - t0) * 1000
            errors.extend(rules_result.violations)
            return ValidationReport(
                ok=False,
                schema_result=schema_result,
                rules_result=rules_result,
                elapsed_ms=elapsed,
                patch_id=patch_id,
                errors=errors,
            )

        # Gate 3: Semantic bridge (ae check)
        gate3 = self._gate3.check(patch)
        elapsed = (time.perf_counter() - t0) * 1000
        if not gate3.ok:
            errors.extend(gate3.errors)
            return ValidationReport(
                ok=False,
                schema_result=schema_result,
                rules_result=rules_result,
                gate3_result=gate3,
                elapsed_ms=elapsed,
                patch_id=patch_id,
                errors=errors,
            )

        return ValidationReport(
            ok=True,
            schema_result=schema_result,
            rules_result=rules_result,
            gate3_result=gate3,
            elapsed_ms=elapsed,
            patch_id=patch_id,
            errors=[],
        )

    def _emit_rejected(
        self, patch: dict, report: ValidationReport, elapsed_ms: float
    ) -> None:
        try:
            self._log.record(AuditLog.event_validation_rejected(
                patch=patch,
                errors=report.errors,
                elapsed_ms=elapsed_ms,
            ))
        except Exception:
            pass  # logging must never crash the caller
