# AI-Safe Execution Infrastructure

> **A structured, sandboxed, and reversible execution layer for AI-driven code modification.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-94%20passed-brightgreen.svg)](#testing)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#requirements)

---

## The Problem

When AI agents generate code and apply it directly to a codebase, several fundamental failure modes emerge:

| Failure | Impact |
|:--------|:-------|
| **Syntax errors** | File is broken, app crashes |
| **Semantic errors** | Logic is wrong, tests fail silently |
| **Uncontrolled execution** | Changes applied directly against live state |
| **No rollback path** | Recovery requires manual `git reset` or worse |
| **Ambiguous intent** | Agent emits free-form diffs, no structured contract |

`aether-runtime` reframes the problem: instead of agents generating raw source code, they emit **structured patch instructions** — a typed JSON contract — that the runtime validates, sandboxes, and commits or rolls back automatically.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Agent                                  │
│              (LLM, Copilot, AutoGPT, etc.)                       │
└────────────────────────┬────────────────────────────────────────┘
                         │  Structured Patch (JSON)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Validation Layer                               │
│                                                                  │
│   Gate 1 ──── JSON Schema (Draft 2020-12)                        │
│               • Action type enforcement                          │
│               • UUID patch_id required                           │
│               • Payload size ceiling (64 KB)                     │
│               • Timeout bounds [100ms – 30s]                     │
│                                                                  │
│   Gate 2 ──── Allow-list & Security Rules                        │
│               • (action, operation) allow-list                   │
│               • No absolute paths / path traversal               │
│               • No os.system / subprocess / eval in payload      │
│               • run_script requires explicit trust elevation      │
└────────────────────────┬────────────────────────────────────────┘
                         │  Valid patch only
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Snapshot System                                │
│                                                                  │
│   • Captures project state → .tar.gz archive                    │
│   • .gitignore + .ai_runtimeignore aware                        │
│   • Always skips: node_modules / venv / __pycache__ / .git      │
│   • SQLite WAL index (concurrent-reader safe)                    │
│   • Cross-platform write lock (fcntl / msvcrt)                  │
│   • Atomic rename: no partial archives ever on disk             │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Sandbox Execution                                │
│                                                                  │
│   Tier 1 ── Cranelift JIT  (zero-syscall, v1.2+)                │
│   Tier 2 ── Wasmtime/WASM  (hardware boundary, v1.1+)           │
│   Tier 3 ── Subprocess     (OS process isolation, v1.0 ✅)       │
│             • Windows: Win32 Job Objects (memory limit)          │
│             • Linux:   resource.setrlimit (RLIMIT_AS + CPU)      │
│             • Timeout enforced via communicate(timeout=)         │
│             • CREATE_NEW_PROCESS_GROUP (Windows signal safety)   │
│             • setsid() + preexec_fn (Unix process group)         │
└────────────────────────┬────────────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
           Success               Failure
              │                     │
              ▼                     ▼
        ┌──────────┐         ┌──────────────┐
        │  Commit  │         │   Rollback   │
        │ snapshot │         │ from archive │
        └──────────┘         └──────────────┘
```

---

## Project Layout

```
aether-lang/
├── sdk/
│   └── python/
│       ├── ai_runtime/
│       │   ├── __init__.py          # Public API: PatchEngine, Sandbox, etc.
│       │   ├── _types.py            # Shared dataclasses (ExecutionResult, SnapshotHandle)
│       │   ├── patch_engine.py      # PatchEngine — validate() + apply() orchestrator
│       │   ├── sandbox.py           # Sandbox — tier-dispatching execution environment
│       │   ├── sandbox_t3.py        # T3 subprocess backend (Windows + Unix)
│       │   ├── sandbox_runner.py    # Worker script run inside child process
│       │   ├── validation/
│       │   │   ├── patch_schema.json  # JSON Schema Draft 2020-12 contract
│       │   │   ├── schema.py          # Gate 1: schema validator
│       │   │   └── rules.py           # Gate 2: allow-list + security rules
│       │   └── snapshot/
│       │       ├── __init__.py
│       │       ├── store.py           # SnapshotStore — capture/restore/commit/prune
│       │       ├── gitignore.py       # .gitignore-aware file collector
│       │       └── lock.py            # Cross-platform advisory file lock
│       └── tests/
│           ├── test_validation.py     # Phase 1: 34 tests
│           ├── test_sandbox.py        # Phase 2: 26 tests (cross-platform)
│           ├── test_sandbox_t3_windows.py  # Phase 2: 3 tests (Windows-only)
│           └── test_snapshot.py       # Phase 3: 31 tests
├── architecture doc/
│   └── AI-Safe-Execution-Infrastructure-Documentation.md
└── crates/                           # Legacy Aether language compiler (Cranelift/Rust)
```

---

## Quick Start

### Install

```bash
pip install aether-runtime
```

### Basic usage

```python
import uuid
from ai_runtime import PatchEngine

engine = PatchEngine()

patch = {
    "schema_version": "1.0",
    "patch_id": str(uuid.uuid4()),       # must be valid UUID v4
    "action": "modify_function",
    "target": {
        "file": "src/app.py",            # relative path only
        "symbol": "calculate_total",
        "symbol_type": "function",
    },
    "changes": {
        "operation": "replace_body",
        "payload": "    return sum(items)",
    },
}

report = engine.validate(patch)
if report.ok:
    engine.apply(patch)
    print(f"✅ Applied in {report.elapsed_ms:.1f}ms")
else:
    print(f"❌ Rejected: {report.first_error}")
```

### Agent CLI

After installation, agents can use Aether without writing Python glue:

```bash
aether validate patch.json
aether apply patch.json
aether rollback <snapshot-id>
```

`aether apply` runs validation through `PatchOrchestrator`, captures a snapshot,
applies the patch, and rolls back if application fails. `ae-safe` is kept as a
backwards-compatible alias for the same CLI.

### With snapshot + auto-rollback

```python
from ai_runtime import PatchEngine, Sandbox

sandbox = Sandbox(project_root=".")
engine  = PatchEngine(sandbox=sandbox)

# Capture state before any change
handle = sandbox.snapshot(patch_id=patch["patch_id"])

report = engine.validate(patch)
if report.ok:
    result = engine.apply(patch)
    if result and result.failed:
        # Execution failed — restore immediately
        sandbox.restore(handle)
        print(f"⚠️  Rolled back: {result.error}")
    else:
        sandbox.commit_snapshot(handle)
        print("✅ Committed")
else:
    print(f"❌ Rejected at gate {report.first_error}")
```

### Execute a sandboxed script

```python
from ai_runtime import Sandbox

with Sandbox(project_root=".") as sb:
    result = sb.execute(
        payload="print('hello from sandbox')",
        timeout_ms=5000,
        memory_limit_mb=128,
    )

print(result.stdout)    # "hello from sandbox"
print(result.tier)      # "t3_subprocess"
print(result.succeeded) # True
```

---

## Patch Schema

Every patch must be a JSON object conforming to [patch_schema.json](ai_runtime/validation/patch_schema.json) (JSON Schema Draft 2020-12).

### Required fields

| Field | Type | Description |
|:------|:-----|:------------|
| `schema_version` | `"1.0"` | Schema version — must be exactly `"1.0"` |
| `patch_id` | UUID v4 string | Unique identifier for idempotency tracking |
| `action` | enum | One of the 7 supported actions below |
| `target.file` | string | **Relative** path to the target file |
| `changes.operation` | string | Operation type (must be in allow-list for action) |
| `changes.payload` | string | Code content, max 64 KB |

### Supported actions

| Action | Operations | Description |
|:-------|:-----------|:------------|
| `modify_function` | `replace_body`, `insert_before`, `insert_after`, `update_logic` | Modify an existing function |
| `add_function` | `replace_body` | Insert a new function |
| `remove_function` | `replace_body` | Delete a function |
| `modify_class` | `replace_body`, `insert_before`, `insert_after` | Modify a class |
| `update_import` | `add_import`, `remove_import` | Add or remove imports |
| `replace_block` | `context_replace` | Context-based block replacement |
| `run_script` | `run` | Execute a script (requires `trust_level='elevated'`) |

### Optional fields

```json
{
  "constraints": {
    "timeout_ms":       5000,
    "memory_limit_mb":  128,
    "allow_network":    false,
    "allow_filesystem": false
  },
  "metadata": {
    "generated_by":  "my-agent-v1",
    "model":         "gemini-2.0-flash",
    "confidence":    0.95
  }
}
```

---

## Security Model

### Two-gate validation

```
Patch JSON
    │
    ▼
Gate 1: JSON Schema ─── rejects malformed structure
    │
    ▼ (valid)
Gate 2: Security Rules
    ├── Operation allow-list ────── unknown (action, operation) pairs rejected
    ├── Path safety ─────────────── absolute paths, ../ traversal blocked
    ├── Payload patterns ────────── os.system / subprocess / eval blocked
    └── Trust elevation ─────────── run_script requires explicit elevated trust
    │
    ▼ (valid)
Execution
```

### What is NOT protected by this layer

- **AI model hallucination**: The runtime validates structure and security, not semantic correctness. A syntactically valid patch can still produce wrong program behaviour.
- **Supply chain attacks**: Malicious packages in the project's dependencies are not audited.
- **Persistent rootkits**: A sufficiently clever payload could attempt to escape the T3 subprocess sandbox. T1 (Cranelift) and T2 (WASM) are the hardened tiers for untrusted code.

---

## Sandbox Tiers

| Tier | Technology | Memory Limit | Syscall Restriction | Status |
|:-----|:-----------|:-------------|:--------------------|:-------|
| **T3** | OS subprocess | Win32 Job Objects / `RLIMIT_AS` | None (process boundary only) | ✅ v1.0 |
| **T2** | Wasmtime/WASI | WASM linear memory | WASI capabilities | 🔜 v1.1 |
| **T1** | Cranelift JIT | Custom memory allocator | Zero syscall surface | 🔜 v1.2 |

Tier selection is automatic (`preferred_tier="auto"`). T3 is always available; T1/T2 are used when their respective runtimes are detected.

---

## Snapshot System

### Storage layout

```
<project_root>/
└── .ai_runtime/
    ├── snapshot.lock        ← Advisory write lock (fcntl / msvcrt)
    ├── snapshots.db         ← SQLite index (WAL mode)
    └── snapshots/
        ├── <uuid>.tar.gz    ← Compressed project archive
        └── ...
```

### What gets snapshotted

The file collector applies a layered exclusion strategy:

```
All files in project_root
        │
        ▼  Tier 1: O(1) frozenset fast-skip
        │  (node_modules, .git, venv, __pycache__, dist, target, ...)
        │
        ▼  Tier 2: pathspec regex
        │  (*.pyc, *.egg-info/, *.so, ...)
        │
        ▼  Tier 3: .gitignore patterns
        │
        ▼  Tier 4: .ai_runtimeignore patterns
        │
        ▼  Tier 5: Size ceiling (> 5 MB per file → skip)
        │
        ▼
   Source files to archive
```

### Snapshot lifecycle

```
capture("patch-123")              status = 'pending'
        │
        ├── patch applied OK ──▶  commit(handle)   status = 'committed'
        │
        └── execution failed ──▶  restore(handle)  status = 'rolled_back'

prune(keep=10)  ──▶  deletes oldest committed/rolled_back archives
```

### Concurrency

Multiple agents can operate on the same project simultaneously:
- `validate()` — fully parallel (read-only, no locking)
- `capture()` / `restore()` — serialized via advisory write lock per project root
- SQLite WAL mode — concurrent readers never block during write

---

## Performance SLOs

| Operation | Target | Measured (this machine) |
|:----------|:-------|:------------------------|
| `validate()` | < 20 ms | **0.12 ms** |
| `capture()` (< 50 MB project) | < 100 ms | passes ✅ |
| `restore()` | < 500 ms | passes ✅ |
| T3 sandbox overhead | < 500 ms | < 200 ms |
| T3 timeout enforcement | within 2× budget | ✅ |

---

## Testing

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run full suite
pytest tests/ -v

# Phase-by-phase
pytest tests/test_validation.py      # Phase 1 — 34 tests
pytest tests/test_sandbox.py         # Phase 2 — 26 tests
pytest tests/test_snapshot.py        # Phase 3 — 31 tests
pytest tests/test_sandbox_t3_windows.py  # Windows-only — 3 tests
```

Current suite: **94 tests, 94 passed** (Python 3.14 / Windows 11)

---

## Roadmap

| Phase | Status | Description |
|:------|:-------|:------------|
| 1 — Validation Layer | ✅ Done | JSON Schema Gate + security rule allow-list |
| 2 — Sandbox (T3) | ✅ Done | Subprocess isolation, Windows Job Objects, Unix rlimit |
| 3 — Snapshot System | ✅ Done | `.tar.gz` archives, SQLite WAL, gitignore-aware, cross-platform locks |
| 4 — Observability | ✅ Done | Structured diffs, audit log, `aether status` CLI |
| 5 — AST Apply Engine | 🔄 Next | Real `modify_function` / `add_function` via `ast` + `libcst` |
| 6 — Node.js SDK | 🔜 Planned | `sdk/node/` TypeScript port |
| 7 — T2 Sandbox (WASM) | 🔜 Planned | Wasmtime WASI integration |
| 8 — T1 Sandbox (JIT) | 🔜 Planned | Cranelift FFI from existing `crates/ae-codegen` |

---

## Requirements

- Python ≥ 3.11
- `jsonschema >= 4.22`
- `pathspec >= 0.12`
- No Docker, no external daemons, no root required

---

## License

MIT — see [LICENSE](LICENSE).
