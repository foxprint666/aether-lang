"""
ai_runtime
~~~~~~~~~~~
AI-Safe Execution Infrastructure — Python SDK

Provides a safe, reversible, auditable execution layer for AI-driven code
modification. AI agents emit structured patches; this runtime validates,
sandboxes, and commits or rolls back the resulting changes.

Quick start:
    from ai_runtime import PatchEngine, Sandbox

    engine = PatchEngine()
    report = engine.validate(my_patch_dict)
    if report.ok:
        engine.apply(my_patch_dict)
    else:
        print(report.first_error)
"""

from .patch_engine import PatchEngine, ValidationReport
from .sandbox import Sandbox
from ._types import ExecutionResult, SnapshotHandle

__version__ = "0.1.0"
__all__ = ["PatchEngine", "ValidationReport", "Sandbox", "ExecutionResult", "SnapshotHandle"]
