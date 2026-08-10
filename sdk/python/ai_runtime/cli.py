"""
ai_runtime.cli
~~~~~~~~~~~~~~
Command-line interface for the AI-Safe Runtime.

Commands:
  ae-safe status              Show high-level system status and recent stats
  ae-safe log                 View the audit log of patch events
  ae-safe snapshots           List available snapshots
  ae-safe diff <snapshot_id>  View the diff between a snapshot and current state
  ae-safe rollback <id>       Restore project state to a specific snapshot
  ae-safe prune               Clean up old snapshots
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from .observability.audit_log import AuditLog
from .observability.diff import compute_diff
from .snapshot.store import SnapshotStore


def _format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-Safe Runtime CLI")
    parser.add_argument("--project", default=".", help="Project root directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    subparsers.add_parser("status", help="Show system status and stats")

    # log
    log_parser = subparsers.add_parser("log", help="View the audit log")
    log_parser.add_argument("-n", "--lines", type=int, default=20, help="Number of lines to show")

    # snapshots
    subparsers.add_parser("snapshots", help="List available snapshots")

    # diff
    diff_parser = subparsers.add_parser("diff", help="View diff against a snapshot")
    diff_parser.add_argument("snapshot_id", help="Snapshot UUID")

    # rollback
    rollback_parser = subparsers.add_parser("rollback", help="Restore to a snapshot")
    rollback_parser.add_argument("snapshot_id", help="Snapshot UUID")

    # prune
    prune_parser = subparsers.add_parser("prune", help="Clean up old snapshots")
    prune_parser.add_argument("--keep", type=int, default=10, help="Number of snapshots to keep")

    args = parser.parse_args()
    project_root = Path(args.project).resolve()
    
    store = SnapshotStore(project_root)
    audit = AuditLog(project_root)

    if args.command == "status":
        print(f"Project: {project_root}")
        print(f"Store:   {store._store_dir}")
        stats = audit.stats()
        if not stats:
            print("No audit events recorded yet.")
        else:
            print("\nAudit Stats:")
            for kind, count in stats.items():
                print(f"  {kind:20}: {count}")

    elif args.command == "log":
        events = audit.tail(args.lines)
        if not events:
            print("Audit log is empty.")
        for e in events:
            # Short format for CLI
            ts_str = _format_time(e.ts)
            print(f"[{ts_str}] {e.kind.value:20} | Patch: {e.patch_id[:8]}... | elapsed: {e.elapsed_ms}ms")
            if e.error:
                print(f"    Error: {e.error}")

    elif args.command == "snapshots":
        snapshots = store.list_snapshots()
        if not snapshots:
            print("No snapshots found.")
        for s in snapshots:
            ts_str = _format_time(s.created_at)
            size_mb = (s.archive_size_bytes or 0) / (1024 * 1024)
            print(f"{s.snapshot_id} | {ts_str} | Status: {s.status:12} | {size_mb:.2f} MB")

    elif args.command == "diff":
        handle = store.load(args.snapshot_id)
        if not handle:
            print(f"Error: Snapshot {args.snapshot_id} not found.", file=sys.stderr)
            return 1
        
        try:
            diff = compute_diff(handle, project_root)
            print(diff.summary)
            if diff.has_changes:
                print("\n" + diff.unified_text())
        except Exception as e:
            print(f"Error computing diff: {e}", file=sys.stderr)
            return 1

    elif args.command == "rollback":
        handle = store.load(args.snapshot_id)
        if not handle:
            print(f"Error: Snapshot {args.snapshot_id} not found.", file=sys.stderr)
            return 1
        
        print(f"Restoring to snapshot {args.snapshot_id}...")
        t0 = time.time()
        store.restore(handle)
        elapsed = (time.time() - t0) * 1000
        print(f"✅ Restored in {elapsed:.1f}ms")

    elif args.command == "prune":
        removed = store.prune(keep=args.keep)
        print(f"Pruned {removed} old snapshot archives. Kept {args.keep}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
