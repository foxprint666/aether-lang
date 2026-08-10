"""
ai_runtime.observability.events
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Structured event types for the audit log.
All events are JSON-serialisable (no datetime objects — use float timestamps).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class EventKind(str, Enum):
    VALIDATION_OK       = "validation_ok"
    VALIDATION_REJECTED = "validation_rejected"
    SNAPSHOT_CAPTURED   = "snapshot_captured"
    EXECUTION_OK        = "execution_ok"
    EXECUTION_FAILED    = "execution_failed"
    ROLLBACK            = "rollback"
    COMMITTED           = "committed"


@dataclass
class AuditEvent:
    """
    A single audit log entry. All fields must be JSON-serialisable.

    Fields:
        kind:           EventKind enum value (stored as string in log)
        patch_id:       UUID of the patch that triggered this event
        ts:             Unix timestamp (float) when event occurred
        action:         Patch action (modify_function, run_script, etc.)
        elapsed_ms:     Time taken for the operation (ms)
        tier:           Sandbox tier used (t1/t2/t3), if applicable
        snapshot_id:    Snapshot UUID, if applicable
        file_count:     Number of files in snapshot, if applicable
        archive_size_bytes: Archive size in bytes, if applicable
        errors:         List of error messages, if applicable
        error:          Single error string (execution failure), if applicable
        stdout_preview: First 200 chars of stdout, if applicable
    """
    kind:               EventKind
    patch_id:           str
    ts:                 float = field(default_factory=time.time)
    action:             Optional[str]  = None
    elapsed_ms:         Optional[float] = None
    tier:               Optional[str]  = None
    snapshot_id:        Optional[str]  = None
    file_count:         Optional[int]  = None
    archive_size_bytes: Optional[int]  = None
    errors:             Optional[list[str]] = None
    error:              Optional[str]  = None
    stdout_preview:     Optional[str]  = None

    def to_dict(self) -> dict:
        """Return a JSON-safe dict (kind as string, None values excluded)."""
        d = asdict(self)
        d["kind"] = self.kind.value
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "AuditEvent":
        """Reconstruct an AuditEvent from a log dict."""
        d = dict(d)
        d["kind"] = EventKind(d["kind"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
