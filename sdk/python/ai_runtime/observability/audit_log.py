"""
ai_runtime.observability.audit_log
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Append-only JSONL audit log for all patch lifecycle events.

Log location: <project_root>/.ai_runtime/audit.jsonl

Format: one JSON object per line (JSONL). Each line is an AuditEvent.to_dict().

Thread/process safety:
  - Each write is a single os.write() call (atomic on POSIX for small buffers).
  - On Windows, file writes < 4KB are atomic at the OS level for local NTFS.
  - No read locks needed — readers just iterate lines.

Retention:
  - The log is append-only and never pruned automatically.
  - Use AuditLog.tail(n) to read recent entries.
  - Use AuditLog.query(patch_id=...) to find events for a specific patch.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional, Iterator

from .events import AuditEvent, EventKind


class AuditLog:
    """
    Append-only structured event log.

    Usage:
        log = AuditLog(project_root=".")
        log.record(AuditEvent(
            kind=EventKind.VALIDATION_OK,
            patch_id=patch["patch_id"],
            action=patch["action"],
            elapsed_ms=report.elapsed_ms,
        ))
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        store_subdir: str = ".ai_runtime",
    ) -> None:
        store_dir = Path(project_root).resolve() / store_subdir
        store_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = store_dir / "audit.jsonl"

    @property
    def path(self) -> Path:
        return self._log_path

    # ── Write ──────────────────────────────────────────────────────────────

    def record(self, event: AuditEvent) -> None:
        """
        Append a single event to the log.
        One os.write() call → atomic on POSIX + NTFS for lines < 4KB.
        """
        line = json.dumps(event.to_dict(), separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        # Open in append mode; O_APPEND makes each write atomic at the OS level
        fd = os.open(str(self._log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)

    # ── Read ───────────────────────────────────────────────────────────────

    def tail(self, n: int = 20) -> list[AuditEvent]:
        """Return the N most recent events (newest last)."""
        return list(self._iter_events())[-n:]

    def query(
        self,
        patch_id: Optional[str] = None,
        kind: Optional[EventKind] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """
        Filter events by patch_id, kind, and/or timestamp.

        Args:
            patch_id: Only return events for this patch UUID.
            kind:     Only return events of this EventKind.
            since:    Only return events with ts >= since (Unix timestamp).
            limit:    Maximum events to return.

        Returns:
            List of matching AuditEvents, chronological order.
        """
        results = []
        for event in self._iter_events():
            if patch_id and event.patch_id != patch_id:
                continue
            if kind and event.kind != kind:
                continue
            if since and event.ts < since:
                continue
            results.append(event)
            if len(results) >= limit:
                break
        return results

    def iter_all(self) -> Iterator[AuditEvent]:
        """Iterate all events from oldest to newest."""
        yield from self._iter_events()

    def stats(self) -> dict:
        """
        Return aggregate counts per EventKind.

        Returns:
            Dict mapping event kind string → count.
        """
        counts: dict[str, int] = {}
        for event in self._iter_events():
            counts[event.kind.value] = counts.get(event.kind.value, 0) + 1
        return counts

    # ── Convenience factory methods ────────────────────────────────────────

    @classmethod
    def event_validation_ok(cls, patch: dict, elapsed_ms: float) -> AuditEvent:
        return AuditEvent(
            kind=EventKind.VALIDATION_OK,
            patch_id=patch.get("patch_id", ""),
            action=patch.get("action"),
            elapsed_ms=round(elapsed_ms, 3),
        )

    @classmethod
    def event_validation_rejected(cls, patch: dict, errors: list[str], elapsed_ms: float) -> AuditEvent:
        return AuditEvent(
            kind=EventKind.VALIDATION_REJECTED,
            patch_id=patch.get("patch_id", "") if isinstance(patch, dict) else "",
            action=patch.get("action") if isinstance(patch, dict) else None,
            elapsed_ms=round(elapsed_ms, 3),
            errors=errors[:10],  # cap at 10 to keep log tidy
        )

    @classmethod
    def event_snapshot_captured(cls, patch_id: str, snapshot_id: str, file_count: int, size_bytes: int) -> AuditEvent:
        return AuditEvent(
            kind=EventKind.SNAPSHOT_CAPTURED,
            patch_id=patch_id,
            snapshot_id=snapshot_id,
            file_count=file_count,
            archive_size_bytes=size_bytes,
        )

    @classmethod
    def event_execution_ok(cls, patch_id: str, tier: str, elapsed_ms: float, stdout: str = "") -> AuditEvent:
        return AuditEvent(
            kind=EventKind.EXECUTION_OK,
            patch_id=patch_id,
            tier=tier,
            elapsed_ms=round(elapsed_ms, 3),
            stdout_preview=stdout[:200] if stdout else None,
        )

    @classmethod
    def event_execution_failed(cls, patch_id: str, tier: str, elapsed_ms: float, error: str) -> AuditEvent:
        return AuditEvent(
            kind=EventKind.EXECUTION_FAILED,
            patch_id=patch_id,
            tier=tier,
            elapsed_ms=round(elapsed_ms, 3),
            error=error[:500] if error else None,
        )

    @classmethod
    def event_committed(cls, patch_id: str, snapshot_id: str) -> AuditEvent:
        return AuditEvent(
            kind=EventKind.COMMITTED,
            patch_id=patch_id,
            snapshot_id=snapshot_id,
        )

    @classmethod
    def event_rollback(cls, patch_id: str, snapshot_id: str) -> AuditEvent:
        return AuditEvent(
            kind=EventKind.ROLLBACK,
            patch_id=patch_id,
            snapshot_id=snapshot_id,
        )

    # ── Private ────────────────────────────────────────────────────────────

    def _iter_events(self) -> Iterator[AuditEvent]:
        if not self._log_path.exists():
            return
        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield AuditEvent.from_dict(json.loads(line))
                except Exception:
                    pass  # skip malformed lines
