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

        # --- Gate 1: Schema ---
        if not schema_result.valid:
            self._rejected_count += 1
            elapsed = (time.perf_counter() - t0) * 1000
            return ValidationReport(
                ok=False,
                schema_result=schema_result,
                rules_result=None,
                elapsed_ms=round(elapsed, 2),
                patch_id=patch.get("patch_id") if isinstance(patch, dict) else None,
            )

        # --- Gate 2: Rules ---
        rules_result = check_rules(patch, trust_level=trust_level)  # type: ignore[arg-type]
        ok = rules_result.valid

        if not ok:
            self._rejected_count += 1
        elapsed = (time.perf_counter() - t0) * 1000

        return ValidationReport(
            ok=ok,
            schema_result=schema_result,
            rules_result=rules_result,
            elapsed_ms=round(elapsed, 2),
            patch_id=patch.get("patch_id") if isinstance(patch, dict) else None,  # type: ignore[union-attr]
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
        handler = _ACTION_HANDLERS.get(action)
        if handler is None:
            raise ValueError(f"No apply handler for action '{action}'")

        handler(patch)
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

def _apply_modify_function(patch: dict) -> None:
    """Replace or update a function body in the target file."""
    # TODO (Phase 2): Parse target file AST, locate symbol, apply changes.payload
    _write_patch_stub(patch, "modify_function")


def _apply_add_function(patch: dict) -> None:
    """Insert a new function into the target file."""
    _write_patch_stub(patch, "add_function")


def _apply_remove_function(patch: dict) -> None:
    """Remove a function from the target file."""
    _write_patch_stub(patch, "remove_function")


def _apply_modify_class(patch: dict) -> None:
    """Modify a class in the target file."""
    _write_patch_stub(patch, "modify_class")


def _apply_update_import(patch: dict) -> None:
    """Add or remove import statements in the target file."""
    _write_patch_stub(patch, "update_import")


def _apply_replace_block(patch: dict) -> None:
    """Context-based block replacement in the target file."""
    _write_patch_stub(patch, "replace_block")


def _apply_run_script(patch: dict, *, sandbox: Optional["Sandbox"] = None) -> "ExecutionResult":
    """
    Execute a run_script patch payload inside the sandbox.
    Routes through the tier-dispatch system (T3 by default).
    """
    from .sandbox import Sandbox

    changes   = patch.get("changes", {})
    payload   = changes.get("payload", "")
    constraints = patch.get("constraints", {})

    sb = sandbox or Sandbox()
    result = sb.execute(
        payload=payload,
        timeout_ms=constraints.get("timeout_ms", 5000),
        memory_limit_mb=constraints.get("memory_limit_mb", 128),
        allow_network=constraints.get("allow_network", False),
        allow_filesystem=constraints.get("allow_filesystem", False),
    )
    return result


def _write_patch_stub(patch: dict, action: str) -> None:
    """
    Placeholder: writes a JSON record of the patch to a staging file.
    This allows end-to-end testing of the validation pipeline before
    the real AST transformation engine (Phase 2) is complete.
    """
    import os
    staging_dir = ".ai_runtime/staged"
    os.makedirs(staging_dir, exist_ok=True)
    patch_id = patch.get("patch_id", "unknown")
    staging_path = os.path.join(staging_dir, f"{patch_id}.json")
    with open(staging_path, "w") as f:
        json.dump({"action": action, "patch": patch, "status": "staged"}, f, indent=2)


_ACTION_HANDLERS: dict[str, object] = {
    "modify_function":  _apply_modify_function,
    "add_function":     _apply_add_function,
    "remove_function":  _apply_remove_function,
    "modify_class":     _apply_modify_class,
    "update_import":    _apply_update_import,
    "replace_block":    _apply_replace_block,
    "run_script":       _apply_run_script,
}
