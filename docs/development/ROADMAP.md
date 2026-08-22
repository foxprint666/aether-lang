# 🗺️ Roadmap

This roadmap uses short milestones. Each milestone should leave the repository working and should update `BACKLOG.md` with completed work.

| Milestone | Title | Status | Goal |
|---|---|---|---|
| **M0** | Repository Audit | ✅ Complete | Inspect architecture, tests, benchmarks, SDKs, crates, docs, CI state. |
| **M1** | Benchmark Infrastructure | ✅ Complete | Create robust benchmark framework and structure. |
| **M2** | Core Correctness Suite | 🔄 In Progress | Expand deterministic Python and JavaScript cases. |
| **M3** | Failure Injection | 🔄 In Progress | Add controlled failures and measure detection/rollback. |
| **M4** | Agent Patch Adapter | ✅ Complete | Define agent-neutral adapter interface (completed for replay/command). |
| **M5** | Real Repository Dataset | 🔄 In Progress | Define reproducible repository/task manifest format (local scaffold complete). |
| **M6** | Control vs Aether Runner | 🔄 In Progress | Run matched control and Aether conditions (partially complete). |
| **M7** | Cross-Language Adapter Architecture | 🔄 In Progress | Document clean adapter contract for Python and JavaScript. |
| **M8** | Statistical Analysis | ✅ Complete | Add processing scripts for numeric aggregations (complete for smoke). |
| **M9** | CI and Regression Testing | 🔲 Planned | Add lightweight CI for unit tests, core correctness tests, adapter tests. |
| **M10** | Reproducibility Release | 🔲 Planned | Convert `benchmark_evidence.md` into a traceable evidence report. |

## 🏁 Deliverables & Exit Criteria

### M0 - Repository Audit ✅ Complete
- Inspect existing architecture, tests, benchmarks, SDKs, crates, docs, and CI state.
- Record current capabilities and gaps in `CURRENT_STATE.md`.
- Establish initial development docs.

### M1 - Benchmark Infrastructure ✅ Complete
- Create `benchmarks/` directory structure.
- Add benchmark configuration and result schema.
- Add executable runner with JSON and CSV output.
- Record git commit SHA and environment metadata.
- Support repeated trials and benchmark subsets.
- Include a deterministic smoke correctness suite.

### M2 - Core Correctness Suite 🔄 In Progress
- Expand deterministic Python and JavaScript cases toward about 30 initial cases.
- Cover valid transformations, invalid transformations, syntax validation, test validation, snapshot creation, snapshot restoration, and rollback.

**Exit Criteria:** Core correctness records include transformation success rate, invalid patch detection rate, snapshot integrity rate, and rollback success rate. Results are generated from actual executions only.

### M3 - Failure Injection 🔄 In Progress
- Add controlled failures: syntax error, runtime error, test failure, undefined variable, broken import, wrong file modification, partial patch, malformed patch, destructive file modification, dependency failure, command failure, timeout, and process crash.

**Exit Criteria:** Raw records include failure type, detection, rollback trigger, rollback success, and repository state comparison. Documentation describes covered and uncovered failure classes.

### M4 - Agent Patch Adapter ✅ Complete
- Define an agent-neutral adapter interface.
- Add a local patch-ingestion adapter before integrating live LLM providers.

**Exit Criteria:** Agent-generated patches can be submitted to Aether without coupling to one model or provider.

### M5 - Real Repository Dataset 🔄 In Progress
- Define reproducible repository/task manifest format.
- Start with pinned public Python and JavaScript repositories that have tests.

**Exit Criteria:** Dataset tasks specify repository, commit, task description, expected behavior, test command, timeout, and language. Upstream repositories are cloned into isolated working copies only.

### M6 - Control vs Aether Runner 🔄 In Progress
- Run matched control and Aether conditions with model, prompt, temperature, task, commit, environment, timeout, and attempts held constant.

**Exit Criteria:** Raw records support direct comparison of task success, test results, patch size, tokens, tool calls, execution time, retries, failure detection, rollback, recovery, and final repository state.

### M7 - Cross-Language Adapter Architecture 🔄 In Progress
- Document and implement a clean adapter contract for Python and JavaScript reference adapters.

**Exit Criteria:** Future TypeScript, Rust, Go, Java, and C/C++ adapters can be added without hard-coding language behavior into benchmark core.

### M8 - Statistical Analysis ✅ Complete
- Add processing scripts for N, mean, median, standard deviation, minimum, maximum, absolute difference, relative difference, and success-rate difference.

**Exit Criteria:** Evidence reports distinguish observed results, inference, and hypothesis.

### M9 - CI and Regression Testing 🔲 Planned
- Add lightweight CI for unit tests, core correctness tests, adapter tests, and benchmark smoke tests.
- Keep full LLM benchmarks manual or scheduled.

**Exit Criteria:** Pull requests run fast smoke checks without requiring expensive agent runs.

### M10 - Reproducibility Release 🔲 Planned
- Convert `benchmark_evidence.md` into a traceable evidence report backed by raw benchmark output.

**Exit Criteria:** A new contributor can clone the repository, run the benchmark, inspect raw results, and reproduce reported measurements.
