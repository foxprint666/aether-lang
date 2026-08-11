"""
ai_runtime.validation.ae_bridge
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Gate 3 — Semantic Bridge: content-hash-aware stability verification.

Architecture
------------
                         patch dict
                              │
          ┌───────────────────▼──────────────────────┐
          │             SemanticGate                  │
          │   (only active when ae_target is present) │
          └───────────────────┬──────────────────────┘
                              │ if replacement_src provided:
                              ▼
                     AeSemaBridge
                   (shells out to `ae`)
                         check --json <tmp_file>
                              │
                         SemaReport
                              │
                    stability_required=True?
                              │
                 ┌────────────┴───────────┐
              PASS (no Union)       REJECT (Union found)

Public API
----------
    gate = SemanticGate()
    result = gate.check(patch)          # BridgeResult
    if not result.ok:
        print(result.errors)

Notes
-----
- If the `ae` binary is not on PATH, the gate is silently skipped
  (logs a warning). This preserves backward compatibility with
  repos that don't have the Aether toolchain installed.
- `ae_target` is *always optional* in the patch schema. Legacy patches
  without it pass Gate 3 automatically.
- Windows: uses `ae.exe` automatically; no special handling required.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SemaDiag:
    """A single diagnostic from ae check --json."""
    severity: str          # "error" | "warning" | "info"
    message: str
    hash_hex: str
    stability_level: int   # 0=mono, 1=dynamic, 2=critical
    suggestion: Optional[str] = None


@dataclass
class SemaReport:
    """
    Parsed result of `ae check --json <file>`.

    Fields:
        ok:           True if no semantic errors were found.
        has_union:    True if any node has a Union type (instability indicator).
        diagnostics:  All SemaDiag entries emitted by ae-sema.
        raw:          The raw JSON dict from ae (for logging).
        elapsed_ms:   Wall-clock time for the ae subprocess call.
    """
    ok: bool
    has_union: bool
    diagnostics: list[SemaDiag]
    raw: dict
    elapsed_ms: float


@dataclass
class BridgeResult:
    """
    Result of Gate 3 (SemanticGate.check).

    Fields:
        ok:           True → patch is safe to proceed.
        skipped:      True → ae binary not found; gate skipped (pass-through).
        errors:       Human-readable rejection reasons.
        report:       The underlying SemaReport (None if skipped or no ae_target).
        elapsed_ms:   Time taken by Gate 3.
    """
    ok: bool
    skipped: bool = False
    errors: list[str] = field(default_factory=list)
    report: Optional[SemaReport] = None
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# AeSemaBridge — low-level subprocess wrapper
# ---------------------------------------------------------------------------

class AeSemaBridge:
    """
    Thin wrapper around the `ae check --json` CLI.

    Usage::

        bridge = AeSemaBridge()
        if bridge.available:
            report = bridge.check_source("fn main() { let x: i32 = 1; }")
    """

    def __init__(self, ae_binary: Optional[str] = None) -> None:
        """
        Args:
            ae_binary: Explicit path to the `ae` binary.
                       If None, searches PATH for `ae` (or `ae.exe` on Windows).
        """
        if ae_binary:
            self._ae = ae_binary
        else:
            self._ae = shutil.which("ae") or shutil.which("ae.exe") or ""

    @property
    def available(self) -> bool:
        """True if the `ae` binary was found and is executable."""
        return bool(self._ae) and os.path.isfile(self._ae)

    def check_source(self, source: str, filename: str = "patch_target.ae") -> SemaReport:
        """
        Write *source* to a temp file, run `ae check --json`, return SemaReport.

        Args:
            source:   Aether source code to check.
            filename: Virtual filename shown in diagnostics.

        Returns:
            SemaReport

        Raises:
            RuntimeError: If the ae binary is not available.
            subprocess.TimeoutExpired: If ae takes > 10 s.
        """
        if not self.available:
            raise RuntimeError(
                "ae binary not found on PATH. Install the Aether toolchain "
                "or set AE_BINARY env var."
            )

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".ae",
            prefix="ae_bridge_",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(source)
            tmp_path = f.name

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [self._ae, "check", tmp_path, "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        elapsed_ms = (time.perf_counter() - t0) * 1000

        # ae check --json exits 0 on success, 1 on sema errors
        raw: dict = {}
        if proc.stdout.strip():
            try:
                raw = json.loads(proc.stdout.strip())
            except json.JSONDecodeError:
                raw = {"parse_error": proc.stdout[:200]}

        diags: list[SemaDiag] = []
        has_union = False
        has_error = False

        for d in raw.get("diagnostics", []):
            sev = d.get("severity", "info").lower()
            sl = int(d.get("stability_level", 0))
            if sev == "error":
                has_error = True
            # Union types show up as stability_level >= 1 or message contains "Union"
            if sl >= 1 or "union" in d.get("message", "").lower():
                has_union = True
            diags.append(SemaDiag(
                severity=sev,
                message=d.get("message", ""),
                hash_hex=d.get("hash_hex", ""),
                stability_level=sl,
                suggestion=d.get("suggestion"),
            ))

        return SemaReport(
            ok=not has_error,
            has_union=has_union,
            diagnostics=diags,
            raw=raw,
            elapsed_ms=elapsed_ms,
        )

    def check_file(self, path: str | Path) -> SemaReport:
        """Run `ae check --json` on an existing file."""
        if not self.available:
            raise RuntimeError("ae binary not found on PATH.")
        source = Path(path).read_text(encoding="utf-8")
        return self.check_source(source, filename=str(Path(path).name))


# ---------------------------------------------------------------------------
# SemanticGate — Gate 3 of the validation pipeline
# ---------------------------------------------------------------------------

class SemanticGate:
    """
    Gate 3: Semantic Bridge verification.

    Called after Gate 1 (schema) and Gate 2 (rules) pass.
    Only activates when the patch contains an `ae_target` block.

    Usage::

        gate = SemanticGate()
        result = gate.check(patch)
        if not result.ok and not result.skipped:
            raise ValidationError(result.errors)
    """

    def __init__(self, ae_binary: Optional[str] = None) -> None:
        """
        Args:
            ae_binary: Override for the `ae` binary path.
                       Checks AE_BINARY env var if not provided.
        """
        _bin = ae_binary or os.environ.get("AE_BINARY", "")
        self._bridge = AeSemaBridge(ae_binary=_bin or None)

    # ── Public ────────────────────────────────────────────────────────────

    def check(self, patch: dict) -> BridgeResult:
        """
        Run Gate 3 on a patch.

        Behaviour:
          - No ae_target  → BridgeResult(ok=True, skipped=True)  (pass-through)
          - ae binary missing → same as above (with a warning in errors)
          - ae_target present, binary found → full stability check

        Args:
            patch: A schema+rules-validated patch dict.

        Returns:
            BridgeResult
        """
        t0 = time.perf_counter()

        ae_target = patch.get("ae_target")
        if not ae_target:
            # No ae_target block — Gate 3 is a no-op.
            return BridgeResult(ok=True, skipped=True, elapsed_ms=0.0)

        if not self._bridge.available:
            # ae toolchain not installed — skip with warning.
            return BridgeResult(
                ok=True,
                skipped=True,
                errors=[
                    "SemanticGate skipped: 'ae' binary not found. "
                    "Install the Aether toolchain for hash-addressed stability checks."
                ],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        replacement_src: Optional[str] = ae_target.get("replacement_src")
        stability_required: bool = ae_target.get("stability_required", True)
        node_hash: str = ae_target.get("node_hash", "")

        # If no replacement source, we can only report the node_hash reference.
        # Gate 3 passes (nothing to check without source).
        if not replacement_src:
            return BridgeResult(
                ok=True,
                skipped=True,
                errors=[],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        # Run the actual semantic check.
        try:
            report = self._bridge.check_source(
                replacement_src,
                filename=f"ae_patch_{node_hash[:8]}.ae",
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return BridgeResult(
                ok=False,
                errors=["SemanticGate timed out (>10s) running 'ae check'."],
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return BridgeResult(
                ok=False,
                errors=[f"SemanticGate subprocess error: {exc}"],
                elapsed_ms=elapsed_ms,
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        errors: list[str] = []

        # Hard semantic errors (parse failures, undefined vars…)
        if not report.ok:
            for d in report.diagnostics:
                if d.severity == "error":
                    errors.append(f"ae-sema error [{d.hash_hex[:8]}]: {d.message}")
            return BridgeResult(ok=False, errors=errors, report=report, elapsed_ms=elapsed_ms)

        # Stability violation (Union type in replacement)
        if stability_required and report.has_union:
            union_diags = [d for d in report.diagnostics if d.stability_level >= 1]
            for d in union_diags:
                hint = f" (hint: {d.suggestion})" if d.suggestion else ""
                errors.append(
                    f"ae-sema stability violation [{d.hash_hex[:8]}]: "
                    f"{d.message}{hint}"
                )
            if not errors:
                errors.append(
                    "SemanticGate: replacement introduces Union/dynamic types. "
                    "Mark the function 'stable' or remove the type ambiguity."
                )
            return BridgeResult(ok=False, errors=errors, report=report, elapsed_ms=elapsed_ms)

        return BridgeResult(ok=True, report=report, elapsed_ms=elapsed_ms)

    # ── Convenience ───────────────────────────────────────────────────────

    @property
    def ae_available(self) -> bool:
        """True if the ae binary is on PATH."""
        return self._bridge.available
