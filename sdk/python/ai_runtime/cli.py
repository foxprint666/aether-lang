"""
ai_runtime.cli
~~~~~~~~~~~~~~
Command-line interface for the AI-Safe Runtime.

Commands:
  aether validate <patch.json>   Validate an Aether patch without applying it
  aether apply <patch.json>      Validate, snapshot, apply, and rollback on failure
  aether rollback <id>           Restore project state to a specific snapshot
  aether status                  Show high-level system status and recent stats
  aether log                     View the audit log of patch events
  aether snapshots               List available snapshots
  aether diff <snapshot_id>      View the diff between a snapshot and current state
  aether prune                   Clean up old snapshots
  aether skill show              Print bundled agent skill instructions
  aether skill install-codex     Install skill into ~/.codex/skills/aether
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from importlib import resources
from pathlib import Path

from .observability.audit_log import AuditLog
from .observability.diff import compute_diff
from .orchestrator import PatchOrchestrator
from .snapshot.store import SnapshotStore


def _format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-Safe Runtime CLI")
    parser.add_argument("--project", default=".", help="Project root directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a patch JSON file")
    validate_parser.add_argument("patch", type=Path, help="Patch JSON file")
    validate_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    validate_parser.add_argument("--trust-level", default="standard", choices=["standard", "elevated"])
    validate_parser.add_argument("--ae-binary", default=None, help="Optional ae binary for semantic checks")

    apply_parser = subparsers.add_parser("apply", help="Validate, snapshot, and apply a patch JSON file")
    apply_parser.add_argument("patch", type=Path, help="Patch JSON file")
    apply_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    apply_parser.add_argument("--trust-level", default="standard", choices=["standard", "elevated"])
    apply_parser.add_argument("--ae-binary", default=None, help="Optional ae binary for semantic checks")
    apply_parser.add_argument("--dry-run", action="store_true", help="Validate only; do not write")

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

    skill_parser = subparsers.add_parser("skill", help="Show, export, or install the bundled agent skill")
    skill_subparsers = skill_parser.add_subparsers(dest="skill_command", required=True)
    skill_subparsers.add_parser("show", help="Print bundled SKILL.md")
    skill_subparsers.add_parser("path", help="Print the bundled SKILL.md path")

    export_parser = skill_subparsers.add_parser("export", help="Copy SKILL.md into a directory")
    export_parser.add_argument("directory", type=Path, help="Destination directory")
    export_parser.add_argument("--force", action="store_true", help="Overwrite an existing SKILL.md")

    codex_parser = skill_subparsers.add_parser("install-codex", help="Install SKILL.md for Codex")
    codex_parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Destination skill directory; defaults to CODEX_HOME/skills/aether or ~/.codex/skills/aether",
    )
    codex_parser.add_argument("--force", action="store_true", help="Overwrite an existing SKILL.md")

    args = parser.parse_args()
    project_root = Path(args.project).resolve()

    if args.command == "skill":
        return _handle_skill_command(args)

    if args.command == "validate":
        patch = _load_patch(args.patch)
        orch = PatchOrchestrator(project_root=project_root, ae_binary=args.ae_binary, dry_run=True)
        report = orch.validate_only(patch, trust_level=args.trust_level)
        payload = {
            "ok": report.ok,
            "patch_id": report.patch_id,
            "elapsed_ms": round(report.elapsed_ms, 2),
            "errors": report.errors,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif report.ok:
            print(f"OK: patch {report.patch_id or '<unknown>'} passed validation")
        else:
            print(f"REJECTED: {_first_error(report.errors)}", file=sys.stderr)
        return 0 if report.ok else 1

    if args.command == "apply":
        patch = _load_patch(args.patch)
        orch = PatchOrchestrator(project_root=project_root, ae_binary=args.ae_binary, dry_run=args.dry_run)
        result = orch.apply(patch, trust_level=args.trust_level)
        payload = {
            "ok": result.ok,
            "patch_id": result.patch_id,
            "snapshot_id": result.snapshot_id,
            "rolled_back": result.rolled_back,
            "elapsed_ms": round(result.elapsed_ms, 2),
            "errors": result.errors,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif result.ok:
            label = "DRY RUN OK" if args.dry_run else "APPLIED"
            print(f"{label}: patch {result.patch_id or '<unknown>'}")
            if result.snapshot_id:
                print(f"Snapshot: {result.snapshot_id}")
        else:
            print(f"FAILED: {result.errors[0] if result.errors else 'apply failed'}", file=sys.stderr)
            if result.rolled_back:
                print("Rolled back to pre-apply snapshot.", file=sys.stderr)
        return 0 if result.ok else 1

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
            ts_str = _format_time(s["created_at"])
            size_mb = (s["archive_size_bytes"] or 0) / (1024 * 1024)
            print(f"{s['id']} | {ts_str} | Status: {s['status']:12} | {size_mb:.2f} MB")

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


def _load_patch(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Error: patch file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(payload, dict):
        print(f"Error: patch file must contain one JSON object: {path}", file=sys.stderr)
        raise SystemExit(2)
    return payload


def _first_error(errors: list[str]) -> str:
    return errors[0] if errors else "validation failed"


def _handle_skill_command(args: argparse.Namespace) -> int:
    if args.skill_command == "show":
        print(_skill_text(), end="")
        return 0

    if args.skill_command == "path":
        print(_skill_path())
        return 0

    if args.skill_command == "export":
        destination = Path(args.directory).expanduser().resolve() / "SKILL.md"
        _copy_skill(destination, force=args.force)
        print(f"Installed Aether skill: {destination}")
        return 0

    if args.skill_command == "install-codex":
        destination_dir = args.dest.expanduser() if args.dest else _default_codex_skill_dir()
        destination = destination_dir.resolve() / "SKILL.md"
        _copy_skill(destination, force=args.force)
        print(f"Installed Aether skill for Codex: {destination}")
        print("Restart Codex or refresh skills if your client does not pick up new skills live.")
        return 0

    print(f"Unknown skill command: {args.skill_command}", file=sys.stderr)
    return 2


def _skill_resource():
    return resources.files("ai_runtime.agent_skill").joinpath("SKILL.md")


def _skill_text() -> str:
    return _skill_resource().read_text(encoding="utf-8")


def _skill_path() -> Path:
    with resources.as_file(_skill_resource()) as path:
        return path


def _copy_skill(destination: Path, *, force: bool) -> None:
    if destination.exists() and not force:
        raise SystemExit(f"Error: {destination} already exists. Use --force to overwrite.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(_skill_resource()) as src:
        shutil.copyfile(src, destination)


def _default_codex_skill_dir() -> Path:
    configured_home = os.environ.get("CODEX_HOME")
    codex_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    return codex_home / "skills" / "aether"


if __name__ == "__main__":
    sys.exit(main())
