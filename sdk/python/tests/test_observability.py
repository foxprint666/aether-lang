import json
import os
import uuid
import time
from pathlib import Path
import pytest

from ai_runtime.observability import AuditLog, AuditEvent, EventKind, compute_diff
from ai_runtime.observability.diff import FileDiff, DiffResult
from ai_runtime._types import SnapshotHandle

def test_audit_event_serialization():
    patch_id = str(uuid.uuid4())
    event = AuditEvent(
        kind=EventKind.VALIDATION_OK,
        patch_id=patch_id,
        action="modify_function",
        elapsed_ms=1.5
    )
    
    d = event.to_dict()
    assert d["kind"] == "validation_ok"
    assert d["patch_id"] == patch_id
    assert d["action"] == "modify_function"
    assert d["elapsed_ms"] == 1.5
    
    # ensure it's json serializable
    s = json.dumps(d)
    assert "validation_ok" in s

    # deserialize
    event2 = AuditEvent.from_dict(json.loads(s))
    assert event2.kind == EventKind.VALIDATION_OK
    assert event2.patch_id == patch_id

def test_audit_log_record_and_query(tmp_path):
    log = AuditLog(project_root=tmp_path)
    
    p1 = str(uuid.uuid4())
    p2 = str(uuid.uuid4())
    
    log.record(AuditEvent(kind=EventKind.VALIDATION_OK, patch_id=p1))
    log.record(AuditEvent(kind=EventKind.VALIDATION_REJECTED, patch_id=p2, errors=["bad"]))
    log.record(AuditEvent(kind=EventKind.SNAPSHOT_CAPTURED, patch_id=p1, snapshot_id="snap1"))
    log.record(AuditEvent(kind=EventKind.EXECUTION_OK, patch_id=p1, tier="t3_subprocess", elapsed_ms=10.0))
    
    assert len(log.tail(10)) == 4
    
    q_p1 = log.query(patch_id=p1)
    assert len(q_p1) == 3
    assert q_p1[0].kind == EventKind.VALIDATION_OK
    
    q_kind = log.query(kind=EventKind.VALIDATION_REJECTED)
    assert len(q_kind) == 1
    assert q_kind[0].patch_id == p2
    
    stats = log.stats()
    assert stats["validation_ok"] == 1
    assert stats["validation_rejected"] == 1
    assert stats["snapshot_captured"] == 1
    assert stats["execution_ok"] == 1

def test_factory_methods():
    patch_id = str(uuid.uuid4())
    patch = {"patch_id": patch_id, "action": "modify_function"}
    
    e1 = AuditLog.event_validation_ok(patch, 1.2)
    assert e1.kind == EventKind.VALIDATION_OK
    assert e1.patch_id == patch_id
    
    e2 = AuditLog.event_validation_rejected(patch, ["error1"], 0.5)
    assert e2.kind == EventKind.VALIDATION_REJECTED
    assert e2.errors == ["error1"]
    
    e3 = AuditLog.event_execution_ok(patch_id, "t3_subprocess", 10.0, "hello world")
    assert e3.stdout_preview == "hello world"

def test_diff_engine(tmp_path):
    from ai_runtime.snapshot.store import SnapshotStore
    store = SnapshotStore(tmp_path)
    
    # Create some initial files
    (tmp_path / "file1.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "file2.py").write_text("def x(): pass\n", encoding="utf-8")
    
    # Capture snapshot
    handle = store.capture(patch_id="p1")
    
    # Make changes
    (tmp_path / "file1.py").write_text("print('hello world')\n", encoding="utf-8") # Modified
    (tmp_path / "file2.py").unlink() # Removed
    (tmp_path / "file3.py").write_text("x = 1\n", encoding="utf-8") # Added
    
    diff = compute_diff(handle, project_root=tmp_path)
    assert diff.has_changes
    assert diff.total_modified == 1
    assert diff.total_removed == 1
    assert diff.total_added == 1
    
    # Check individual file diffs
    files = {fd.path: fd for fd in diff.files}
    assert files["file1.py"].status == "modified"
    assert "print('hello world')" in files["file1.py"].unified_diff
    assert files["file2.py"].status == "removed"
    assert files["file3.py"].status == "added"
    
    # Summary
    assert "3 files changed" in diff.summary
