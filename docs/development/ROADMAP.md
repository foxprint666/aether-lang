# Roadmap

This roadmap uses short milestones. Each milestone should leave the repository working and should update `BACKLOG.md` with completed work.

## M0 - Repository Audit

Status: complete.

Deliverables:

- Inspect existing architecture, tests, benchmarks, SDKs, crates, docs, and CI state.
- Record current capabilities and gaps in `CURRENT_STATE.md`.
- Establish initial development docs.

## M1 - Benchmark Infrastructure

Status: complete.

Deliverables:

- Create `benchmarks/` directory structure.
- Add benchmark configuration and result schema.
- Add executable runner with JSON and CSV output.
- Record git commit SHA and environment metadata.
- Support repeated trials and benchmark subsets.
- Include a deterministic smoke correctness suite.

## M2 - Core Correctness Suite

Status: partially complete.

Goal:

- Expand deterministic Python and JavaScript cases toward about 30 initial cases.
- Cover valid transformations, invalid transformations, syntax validation, test validation, snapshot creation, snapshot restoration, and rollback.

Exit criteria:

- Core correctness records include transformation success rate, invalid patch detection rate, snapshot integrity rate, and rollback success rate.
- Results are generated from actual executions only.

## M3 - Failure Injection

Status: partially complete.

Goal:

- Add controlled failures: syntax error, runtime error, test failure, undefined variable, broken import, wrong file modification, partial patch, malformed patch, destructive file modification, dependency failure, command failure, timeout, and process crash.

Exit criteria:

- Raw records include failure type, detection, rollback trigger, rollback success, and repository state comparison.
- Documentation describes covered and uncovered failure classes.

## M4 - Agent Patch Adapter

Status: complete for replay and command-adapter infrastructure, including a small live Gemini provider smoke run.

Goal:

- Define an agent-neutral adapter interface.
- Add a local patch-ingestion adapter before integrating live LLM providers.

Exit criteria:

- Agent-generated patches can be submitted to Aether without coupling to one model or provider.

## M5 - Real Repository Dataset

Status: complete for local repository-derived fixture scaffolding; external pinned repositories still pending.

Goal:

- Define reproducible repository/task manifest format.
- Start with pinned public Python and JavaScript repositories that have tests.

Exit criteria:

- Dataset tasks specify repository, commit, task description, expected behavior, test command, timeout, and language.
- Upstream repositories are cloned into isolated working copies only.

## M6 - Control vs Aether Runner

Status: partially complete for deterministic and replay-agent tasks.

Goal:

- Run matched control and Aether conditions with model, prompt, temperature, task, commit, environment, timeout, and attempts held constant.

Exit criteria:

- Raw records support direct comparison of task success, test results, patch size, tokens, tool calls, execution time, retries, failure detection, rollback, recovery, and final repository state.

## M7 - Cross-Language Adapter Architecture

Status: partially complete.

Goal:

- Document and implement a clean adapter contract for Python and JavaScript reference adapters.

Exit criteria:

- Future TypeScript, Rust, Go, Java, and C/C++ adapters can be added without hard-coding language behavior into benchmark core.

## M8 - Statistical Analysis

Status: complete for benchmark smoke.

Goal:

- Add processing scripts for N, mean, median, standard deviation, minimum, maximum, absolute difference, relative difference, and success-rate difference.

Exit criteria:

- Evidence reports distinguish observed results, inference, and hypothesis.

## M9 - CI and Regression Testing

Goal:

- Add lightweight CI for unit tests, core correctness tests, adapter tests, and benchmark smoke tests.
- Keep full LLM benchmarks manual or scheduled.

Exit criteria:

- Pull requests run fast smoke checks without requiring expensive agent runs.

## M10 - Reproducibility Release

Goal:

- Convert `benchmark_evidence.md` into a traceable evidence report backed by raw benchmark output.

Exit criteria:

- A new contributor can clone the repository, run the benchmark, inspect raw results, and reproduce reported measurements.
