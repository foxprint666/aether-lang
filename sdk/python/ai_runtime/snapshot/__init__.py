"""
ai_runtime.snapshot
~~~~~~~~~~~~~~~~~~~~
Snapshot & Rollback subsystem.

Public surface:
    from ai_runtime.snapshot import SnapshotStore
"""

from .store import SnapshotStore

__all__ = ["SnapshotStore"]
