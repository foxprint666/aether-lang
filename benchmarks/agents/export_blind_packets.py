#!/usr/bin/env python
"""Export blind external-agent descriptors from pinned repository caches."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .blind_protocol import build_blind_descriptor
except ImportError:
    from blind_protocol import build_blind_descriptor


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_ROOT = REPO_ROOT / ".tmp" / "benchmark-repositories"
FORBIDDEN_KEYS = {"test_command", "patch"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export blind external-agent trial packets.")
    parser.add_argument("--manifest", type=Path, required=True, help="External task manifest JSON.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Destination for packet JSON files.")
    parser.add_argument("--trials", type=int, default=1, help="Packets to export per task.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT, help="Pinned checkout cache root.")
    args = parser.parse_args()

    if args.trials < 1:
        parser.error("--trials must be >= 1")

    manifest_path = args.manifest.resolve()
    cache_root = args.cache_root.resolve()
    output_dir = args.output_dir.resolve()
    manifest = load_object(manifest_path)
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError(f"Manifest {manifest_path} must contain a tasks array")

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for task_value in tasks:
        if not isinstance(task_value, dict):
            raise ValueError("Every manifest task must be an object")
        if task_value.get("category") != "external_agent_patch":
            continue
        task = task_value
        repository = required_string(task, "repository")
        repository_manifest = resolve_repository_manifest(required_string(task, "repository_manifest"))
        pinned = load_object(repository_manifest)
        git_source = pinned.get("source")
        if not isinstance(git_source, dict) or git_source.get("type") != "git":
            raise ValueError(f"Repository manifest {repository_manifest} must define a git source")
        url = required_string(git_source, "url")
        commit = required_string(git_source, "commit")
        if re.fullmatch(r"[0-9a-fA-F]{40}", commit) is None:
            raise ValueError(f"Repository manifest {repository_manifest} must pin a 40-character commit")

        checkout = cache_root / repository_cache_key(repository, commit)
        validate_cached_checkout(checkout, url, commit)
        source_file = required_string(task, "source_file")
        source_path = (checkout / source_file).resolve()
        if checkout.resolve() not in source_path.parents or not source_path.is_file():
            raise ValueError(f"Source file is missing or outside cached checkout: {source_file}")
        source = source_path.read_text(encoding="utf-8")

        task_id = required_string(task, "task_id")
        for trial in range(1, args.trials + 1):
            descriptor = build_blind_descriptor(
                task=task_id,
                trial=trial,
                language=required_string(task, "language"),
                repository=repository,
                fixture=required_string(task, "fixture"),
                source_file=source_file,
                description=required_string(task, "description"),
                source=source,
            )
            assert_blind(descriptor)
            packet_path = output_dir / f"{safe_name(task_id)}-trial-{trial}.json"
            packet_path.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            written += 1

    print(f"Exported {written} blind packets to {output_dir}")
    return 0


def resolve_repository_manifest(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def repository_cache_key(repository: str, commit: str) -> str:
    safe_repository = re.sub(r"[^A-Za-z0-9_.-]+", "-", repository).strip("-._")
    return f"{safe_repository or 'repository'}-{commit.lower()}"


def validate_cached_checkout(checkout: Path, url: str, commit: str) -> None:
    marker = checkout / ".aether-benchmark-cache.json"
    if not marker.is_file():
        raise FileNotFoundError(f"Pinned checkout is not cached: {checkout}")
    marker_value = load_object(marker)
    if marker_value != {"url": url, "commit": commit}:
        raise ValueError(f"Cached checkout marker does not match pinned source: {checkout}")


def assert_blind(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS or key.startswith("expected_") or key == "acceptance":
                raise ValueError(f"Blind packet contains forbidden key: {key}")
            assert_blind(child)
    elif isinstance(value, list):
        for child in value:
            assert_blind(child)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"Expected non-empty string field: {key}")
    return item


def safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    if not result:
        raise ValueError("Task id cannot produce an empty packet filename")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
