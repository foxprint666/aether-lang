# Phase 7 Public Benchmark Report

Generated: `2026-08-16T05:53:22.131Z`
Commit: `a5d95ce5137e5f404873703f3c36c4f0ff42c6e8`
Phase 7 ready: `true`

## Gate Summary

- Phase 4 real repositories: `18` records, `100.0%` success.
- Phase 5 cross-language: `54` records, `100.0%` success.
- Phase 6 A/B agent: `48` records, `100.0%` success.
- External pinned repositories: `132` records across `5` repositories and `12` tasks, `1.0` success rate.
- External verification levels: `{"behavior": 96, "safety": 12, "syntax": 24}`.
- External rollback success: `1.0`.
- Tested proof scope: `321/321` passed.
- Conservative proof score: `72.572%` (promising evidence, still limited).

## Efficiency

- Live output-token savings: `83.258595%` where provider telemetry exists.
- State vs Aether matched records: `47`.
- State mean execution: `117.932596 ms`.
- Full Aether mean execution: `177.197979 ms`.
- Hybrid records: `59`, success rate `1.0`.
- Hybrid selected modes: `{"aether": 28, "control": 25, "state": 6}`.
- Offline estimated patch-vs-rewrite output savings: `13.068592%`.
- External hybrid output-token savings: `75.013594%`.
- External hybrid total-token savings: `28.841942%`.
- External hybrid emitted-byte savings: `79.423409%`.
- External hybrid edit-to-verified delta: `41.322533 ms`.

## Reproduction Commands

```bash
python benchmarks/run.py --suite smoke --mode all-modes --trials 1 --experiment-id ci-smoke-local
```
```bash
python benchmarks/run.py --suite all --mode hybrid --trials 1 --experiment-id hybrid-threshold-all-smoke
```
```bash
python benchmarks/run.py --suite external-repository --mode all-modes --trials 3 --allow-network-repos --experiment-id external-matrix-allmodes-trials3-v3
```
```bash
python benchmarks/analysis/phase_gates.py --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json
```
```bash
python benchmarks/analysis/phase7_readiness.py --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json --state-results benchmarks/results/raw/state-fastpath-correctness.json benchmarks/results/raw/state-fastpath-agent-trials3.json benchmarks/results/raw/state-fastpath-realrepo-trials3.json benchmarks/results/raw/state-fastpath-all-smoke.json --hybrid-results benchmarks/results/raw/hybrid-threshold-all-smoke.json benchmarks/results/raw/hybrid-threshold-smoke-v2.json --external-results benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json --proof-results benchmarks/results/raw/live-openrouter-smoke-v3.json benchmarks/results/raw/phase6-expanded-all.json benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json benchmarks/results/raw/phase4-realrepo-done-trials3.json benchmarks/results/raw/phase5-crosslang-done-trials3.json benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json
```
```bash
python benchmarks/analysis/phase7_bundle.py --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json --state-results benchmarks/results/raw/state-fastpath-correctness.json benchmarks/results/raw/state-fastpath-agent-trials3.json benchmarks/results/raw/state-fastpath-realrepo-trials3.json benchmarks/results/raw/state-fastpath-all-smoke.json --hybrid-results benchmarks/results/raw/hybrid-threshold-all-smoke.json benchmarks/results/raw/hybrid-threshold-smoke-v2.json --external-results benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json --token-results benchmarks/results/raw/token-estimate-all-smoke.json --proof-results benchmarks/results/raw/live-openrouter-smoke-v3.json benchmarks/results/raw/phase6-expanded-all.json benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json benchmarks/results/raw/phase4-realrepo-done-trials3.json benchmarks/results/raw/phase5-crosslang-done-trials3.json benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json
```

## Evidence Files

- `phase4`:
  - `benchmarks/results/raw/phase4-realrepo-done-trials3.json` sha256 `71cad4ffd7b970febc643f29871516d3d45219f3c45072b991683131d3a04a38` bytes `38791`
- `phase5`:
  - `benchmarks/results/raw/phase5-crosslang-done-trials3.json` sha256 `ac2409a33373f227a67119b6eaaaaa835331dee81d497d2d8d494b4e353d5918` bytes `135833`
- `phase6`:
  - `benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json` sha256 `b32afa54e96614c1f9a48ab8ac4bbcc28dd7ef0c274ea68d6161e4464263cbe1` bytes `119434`
- `state_results`:
  - `benchmarks/results/raw/state-fastpath-correctness.json` sha256 `dcb876948e71da5b6a0c76031e75d9adab59645c0e84057ce32520ab4253c844` bytes `74017`
  - `benchmarks/results/raw/state-fastpath-agent-trials3.json` sha256 `fe4ef9b3d66699c935f21dfa41205810d901fae0a9d35e37ce07010bd7eb50c7` bytes `152040`
  - `benchmarks/results/raw/state-fastpath-realrepo-trials3.json` sha256 `5aa80becc51326588cfe9f492fbf1b89d44e154205d17495558e738864b7171d` bytes `80967`
  - `benchmarks/results/raw/state-fastpath-all-smoke.json` sha256 `0464431293174e48d8e63ec0733909bd2c600c14bf0713e6c9d41759c8c49126` bytes `187137`
- `hybrid_results`:
  - `benchmarks/results/raw/hybrid-threshold-all-smoke.json` sha256 `74ada52b4402cd6bdeaa7661bfa33ca6a3f4d9874910bae3340b20098f67c052` bytes `114083`
  - `benchmarks/results/raw/hybrid-threshold-smoke-v2.json` sha256 `1ad56fb6123659645b37fbae9b0ee933ed138ebffafd46ad39ca8b5572d29081` bytes `42369`
- `external_results`:
  - `benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json` sha256 `2fb91871344d7d1d50d05e4995781afa942c78156281d9e93affdd7e7913ad9f` bytes `405080`
- `token_results`:
  - `benchmarks/results/raw/token-estimate-all-smoke.json` sha256 `f07d384656959563eb17ef0869de8475344e2e06514aef0542a4533f52374620` bytes `201844`
- `proof_results`:
  - `benchmarks/results/raw/live-openrouter-smoke-v3.json` sha256 `cbb76292904b13eac745712b1791865b1c9d6b18dc9474b55bda12c870d31981` bytes `20539`
  - `benchmarks/results/raw/phase6-expanded-all.json` sha256 `ae2f6ea745c24de9dd092f8534a4d755231cef8003c898302e90b7b4dab90ed5` bytes `142175`
  - `benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json` sha256 `b32afa54e96614c1f9a48ab8ac4bbcc28dd7ef0c274ea68d6161e4464263cbe1` bytes `119434`
  - `benchmarks/results/raw/phase4-realrepo-done-trials3.json` sha256 `71cad4ffd7b970febc643f29871516d3d45219f3c45072b991683131d3a04a38` bytes `38791`
  - `benchmarks/results/raw/phase5-crosslang-done-trials3.json` sha256 `ac2409a33373f227a67119b6eaaaaa835331dee81d497d2d8d494b4e353d5918` bytes `135833`
  - `benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json` sha256 `2fb91871344d7d1d50d05e4995781afa942c78156281d9e93affdd7e7913ad9f` bytes `405080`

## Limitations

- The local reproducible scope is not a universal claim about all repositories or all agents.
- Live token/cost telemetry is limited to small provider smoke evidence; offline token estimates are reported separately.
- State mode measures raw transition efficiency without validation, snapshots, or rollback.
- Hybrid mode is a threshold policy for product routing, not an externally validated universal optimum.
- External coverage is five pinned repositories and twelve tasks; it is stronger than a smoke test but still not a representative sample of all software projects.
