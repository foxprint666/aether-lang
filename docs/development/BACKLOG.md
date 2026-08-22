# Backlog

## Completed

- M0: Audited repository layout, README, benchmark evidence, architecture docs, Cargo workspace, Python SDK, Node SDK, tests, snapshot/rollback code, sandbox code, AST patching, security documentation, and CI state.
- M0: Created `docs/development/CURRENT_STATE.md`.
- M0: Created `docs/development/ROADMAP.md`.
- M0: Created `docs/development/BACKLOG.md`.
- M0: Created `docs/development/TESTING_STRATEGY.md`.
- M1: Created benchmark directory scaffold.
- M1: Added benchmark result schema.
- M1: Added benchmark runner with JSON, CSV, metadata, repeated trials, and suite selection.
- M1: Added deterministic correctness smoke task manifest.
- M2: Expanded the correctness benchmark to eight Python cases covering successful transformations, validation rejection, and rollback-on-apply-failure behavior.
- M2: Added manifest-driven task execution and expected-negative benchmark scoring.
- M2: Added four JavaScript correctness cases through a Node/Recast benchmark adapter.
- M2: Added LF-normalized fixture writing for reproducible context matching across platforms.
- M2: Added task categories and aggregate correctness metrics to benchmark summaries.
- M2: Added `benchmarks/analysis/summarize.py` for raw-result statistical summaries.
- M3: Added executable failure-injection suite with Python and JavaScript syntax/runtime/import/path/AST-apply failures.
- M3: Added failure type and failure detection fields to benchmark records.
- M3: Added `failure_detection_rate` to benchmark summaries.
- M4: Added deterministic replay-agent adapter for agent patch-ingestion benchmarks.
- M4: Added executable `agent` suite and included it in `all`.
- M5: Added command-backed agent adapter contract with retry, token, tool-call, latency, cost, and model metadata ingestion.
- M5: Added mock provider command and verified command-agent usage metadata capture.
- M5: Added matched replay-agent `control` vs `aether` runs.
- M5: Added snapshot-backed JavaScript Aether benchmark adapter path.
- M5: Added JavaScript invalid-patch and rollback correctness cases.
- M5: Added local real-repository manifest scaffold and smoke task.
- M5: Added GitHub Actions benchmark smoke workflow.
- M6: Fixed Python `update_import` ordering so new imports are inserted after module docstrings, `from __future__` imports, and the existing import block.
- M6: Added a regression test and restored the real-repository import benchmark that exposed the ordering bug.
- M6: Added OpenAI Responses API provider command wrapper for live command-agent runs.
- M6: Added Gemini `generateContent` provider command wrapper for live command-agent runs.
- M6: Added external git repository manifest support behind `--allow-network-repos`.
- M7: Expanded external validation to five pinned Python/JavaScript repositories, twelve tasks, cached checkouts, full-file control baselines, three-trial efficiency reporting, and external rollback cases.
- M7: Added eight previously unpublished external-agent tasks, source-only blind packets, hidden tests, hash-locked replay, three independent generation trials, and a manual CI workflow. The first final run passed 16/24 generated patches without oracle normalization.
- M7: Added paired blind agent-generation evidence comparing source-only Aether patch outputs against matched full-file outputs. The first 7-task run passed 10/21 Aether patch outputs and 17/21 full-file outputs; Aether used 82.351% fewer estimated output tokens.
- M7: Added compiler-transition planner documentation and a benchmark analyzer that estimates dynamic full-file/state/Aether/hybrid/graph-scoped method selection. On the external matrix, measured token-first routing projected 28.964% total-token savings; synthetic 80% graph-context reduction projected 74.479% total-token savings.
- M6: Added broader CI workflow for Rust, focused Python, and focused Node checks.
- M6: Added Codex session evidence documenting real AI-agent work performed in this development session.

## Next Up

- M2: Expand Python correctness cases toward the full initial target of about 30 cases.
- M2: Expand JavaScript correctness cases and add Node validation/rollback coverage once a stable snapshot-backed Node orchestrator path exists.
- M2: Add more invalid patch detection cases for malformed JSON, wrong files, and incompatible transformations.
- M2: Add benchmark smoke invocation to CI after CI is introduced.
- M3: Add timeout, command failure, partial patch, destructive modification, and dependency failure variants.
- M3: Add recovery-time measurement around rollback.
- Add provider-specific command wrappers for Anthropic/local agents.
- Expand live/provider matched control-vs-Aether experiments beyond the current Gemini smoke record.
- Add dependency-installed external project test suites and multi-file tasks beyond the current pinned matrix.
- Add fresh unpublished blind tasks, OS-enforced agent isolation, and patch-generation repair loops to close the paired blind success gap versus full-file generation.
- Add real graph-context integration to measure Graphify-style input savings rather than relying on synthetic planner projections.
- Add latency-weighted planner benchmarks so the system can choose speed-first, cost-first, or safety-first methods dynamically.

## Epic A - Benchmark Infrastructure

- Extend statistical aggregation with control-vs-Aether difference calculations.
- Add immutable result naming guidance.
- Add processed summary schema.
- Add benchmark versioning policy.
- Add environment lockfile guidance.

## Epic B - Core Correctness

- Cover rename-like transformations where current AST engines support them or explicitly document lack of support.
- Cover import add/remove behavior in Python and JavaScript.
- Cover multiple-file patch scenarios once the patch schema supports them cleanly.
- Measure syntax validation and test validation separately.

## Epic C - Agent Patch Evaluation

- Define agent adapter interface.
- Add file-based patch ingestion adapter.
- Add retry-result format.
- Add prompt/config provenance fields without depending on one LLM provider.

## Epic D - Failure Injection

- Add syntax failure cases.
- Add runtime failure cases.
- Add dependency failure and command failure cases.
- Add timeout and process crash cases.
- Compute false acceptance rate, failure detection rate, and rollback success rate.

## Epic E - Real Repository Benchmark

- Define repository manifest schema.
- Select small/medium Python repositories.
- Select small/medium JavaScript repositories.
- Pin commits and test commands.
- Add isolated checkout/cache policy.

## Epic F - Cross Language

- Document adapter contract: parse, validate, transform, serialize, diagnostics, and test command.
- Promote existing Python and Node AST engines as reference adapters.
- Add contributor guide for new language adapters.

## Epic G - Agent A/B Evaluation

- Implement matched control/Aether runner.
- Record retries, token counts, tool calls, latency, and final repository state.
- Add repeated trial support for agent tasks.

## Epic H - Documentation and Open Source

- Create `BENCHMARK_METHODOLOGY.md`.
- Create `CONTRIBUTING_BENCHMARKS.md`.
- Rewrite `benchmark_evidence.md` into preliminary evidence, reproducible results, limitations, and future experiments.
- Add CI documentation after workflows exist.
