"""
ai_runtime.patch_engine
~~~~~~~~~~~~~~~~~~~~~~~~
PatchEngine: the primary interface for AI agents submitting structured patches.

Usage:
    from ai_runtime import PatchEngine

    engine = PatchEngine()
    result = engine.validate(patch_dict)
    if result.ok:
        engine.apply(patch_dict)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .validation.schema import validate_schema, validate_schema_from_string, ValidationResult
from .validation.rules import check_rules, RulesResult

if TYPE_CHECKING:
    from .sandbox import Sandbox, ExecutionResult


@dataclass
class ValidationReport:
    """
    Combined result of schema + rules validation.
    Contains timing data for observability (target: < 20ms total).
    """
    ok: bool
    schema_result: ValidationResult
    rules_result: Optional[RulesResult]
    elapsed_ms: float
    patch_id: Optional[str] = None

    @property
    def errors(self) -> list[str]:
        msgs: list[str] = []
        msgs.extend(self.schema_result.errors)
        if self.rules_result is not None:  # NOT truthiness -- __bool__ returns .valid
            msgs.extend(v.message for v in self.rules_result.violations)
        return msgs

    @property
    def first_error(self) -> Optional[str]:
        return self.errors[0] if self.errors else None

    def __bool__(self) -> bool:
        return self.ok


class PatchEngine:
    """
    Validates and applies structured AI patch instructions.

    The validation pipeline runs two gates in order:
      Gate 1 — JSON Schema (Draft 2020-12): structural correctness
      Gate 2 — Allow-list & security rules: operational safety

    Only patches passing both gates proceed to apply().

    Args:
        sandbox: Optional Sandbox instance for executing run_script patches.
                 If None, a default Sandbox() is created lazily on first use.
        project_root: Working directory for sandbox execution (defaults to cwd).
    """

    def __init__(
        self,
        sandbox: Optional["Sandbox"] = None,
        project_root: Optional[str] = None,
    ) -> None:
        self._applied_count  = 0
        self._rejected_count = 0
        self._sandbox        = sandbox
        self._project_root   = project_root
        self._audit_log      = None

    def _get_audit_log(self):
        if self._audit_log is None:
            from .observability import AuditLog
            self._audit_log = AuditLog(project_root=self._project_root or ".")
        return self._audit_log

    def _get_sandbox(self) -> "Sandbox":
        """Lazy-initialize sandbox on first use."""
        if self._sandbox is None:
            from .sandbox import Sandbox
            self._sandbox = Sandbox(
                project_root=self._project_root or "."
            )
        return self._sandbox

    def validate(
        self,
        patch: dict | str,
        *,
        trust_level: str = "standard",
    ) -> ValidationReport:
        """
        Validate a patch through both gates. Read-only; no state changes.

        Args:
            patch: A patch dict or raw JSON string.
            trust_level: 'standard' or 'elevated' (required for run_script).

        Returns:
            ValidationReport with ok=True if all gates pass.

        Performance target: < 20ms.
        """
        t0 = time.perf_counter()

        # --- Parse if given a raw string ---
        if isinstance(patch, str):
            schema_result = validate_schema_from_string(patch)
            if schema_result.valid:
                patch = json.loads(patch)
        else:
            schema_result = validate_schema(patch)

        patch_dict = patch if isinstance(patch, dict) else {}

        # --- Gate 1: Schema ---
        if not schema_result.valid:
            self._rejected_count += 1
            elapsed = (time.perf_counter() - t0) * 1000
            
            log = self._get_audit_log()
            log.record(log.event_validation_rejected(
                patch=patch_dict,
                errors=schema_result.errors,
                elapsed_ms=round(elapsed, 2)
            ))
            
            return ValidationReport(
                ok=False,
                schema_result=schema_result,
                rules_result=None,
                elapsed_ms=round(elapsed, 2),
                patch_id=patch_dict.get("patch_id")
            )

        # --- Gate 2: Rules ---
        rules_result = check_rules(patch_dict, trust_level=trust_level)  # type: ignore[arg-type]
        ok = rules_result.valid

        elapsed = (time.perf_counter() - t0) * 1000
        log = self._get_audit_log()

        if not ok:
            self._rejected_count += 1
            log.record(log.event_validation_rejected(
                patch=patch_dict,
                errors=[v.message for v in rules_result.violations],
                elapsed_ms=round(elapsed, 2)
            ))
        else:
            log.record(log.event_validation_ok(
                patch=patch_dict,
                elapsed_ms=round(elapsed, 2)
            ))

        return ValidationReport(
            ok=ok,
            schema_result=schema_result,
            rules_result=rules_result,
            elapsed_ms=round(elapsed, 2),
            patch_id=patch_dict.get("patch_id"),
        )

    def apply(self, patch: dict) -> None:
        """
        Apply a validated patch to the target file.

        IMPORTANT: Always call validate() first. apply() trusts that the patch
        is already validated and does NOT re-run validation internally.
        Call the full pipeline via PatchEngine.process() to get both.

        Args:
            patch: A validated patch dict.

        Raises:
            ValueError: If the patch is missing required keys (defensive check).
        """
        action   = patch.get("action")
        target   = patch.get("target", {})
        changes  = patch.get("changes", {})

        if not action or not target.get("file"):
            raise ValueError("apply() received a patch missing 'action' or 'target.file'")

        # run_script routes through the live sandbox (Phase 2)
        if action == 'run_script':
            result = _apply_run_script(patch, sandbox=self._get_sandbox())
            self._applied_count += 1
            return result

        # All other actions dispatch to their stub/AST handler
        # All other actions dispatch to the AST handler
        from .ast.engine import apply_patch
        apply_patch(patch, self._project_root or ".")

        self._applied_count += 1

    def process(
        self,
        patch: dict | str,
        *,
        trust_level: str = "standard",
    ) -> ValidationReport:
        """
        Full pipeline: validate → apply (if valid).

        Returns the ValidationReport regardless of outcome.
        If ok=True, the patch has been applied.
        """
        report = self.validate(patch, trust_level=trust_level)
        if report.ok and isinstance(patch, str):
            patch = json.loads(patch)
        if report.ok:
            self.apply(patch)  # type: ignore[arg-type]
        return report

    @property
    def stats(self) -> dict:
        """Return cumulative validation/apply statistics for this engine instance."""
        return {
            "applied":  self._applied_count,
            "rejected": self._rejected_count,
            "total":    self._applied_count + self._rejected_count,
        }


# ---------------------------------------------------------------------------
# Action handlers (Phase 1: stubs with clear TODOs for Phase 2)
# ---------------------------------------------------------------------------

# AST actions are routed directly to ast.engine.apply_patch


def _apply_run_script(patch: dict, *, sandbox: Optional["Sandbox"] = None) -> "ExecutionResult":
    """
    Execute a run_script patch payload inside the sandbox.
    Routes through the tier-dispatch system (T3 by default).
    """
    from .sandbox import Sandbox

    changes   = patch.get("changes", {})
    payload   = changes.get("payload", "")
    constraints = patch.get("constraints", {})
    patch_id  = patch.get("patch_id", "")

    sb = sandbox or Sandbox()
    result = sb.execute(
        payload=payload,
        patch_id=patch_id,
        timeout_ms=constraints.get("timeout_ms", 5000),
        memory_limit_mb=constraints.get("memory_limit_mb", 128),
        allow_network=constraints.get("allow_network", False),
        allow_filesystem=constraints.get("allow_filesystem", False),
    )
    return result



