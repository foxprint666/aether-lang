"""
ai_runtime.observability
~~~~~~~~~~~~~~~~~~~~~~~~~
Audit log, diff generation, and event tracking for the AI-Safe runtime.

Public surface:
    from ai_runtime.observability import AuditLog, AuditEvent, DiffResult
"""

from .audit_log import AuditLog
from .events import AuditEvent, EventKind
from .diff import DiffResult, compute_diff

__all__ = ["AuditLog", "AuditEvent", "EventKind", "DiffResult", "compute_diff"]
