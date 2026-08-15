# Testing Strategy

## Principles

- Prefer executable tests and benchmarks over claims in prose.
- Record raw results from actual executions only.
- Keep CI smoke tests fast and deterministic.
- Keep full agent and real-repository benchmarks manual or scheduled.
- Report limitations beside every benchmark family.
- Treat absence of observed failure as an observation, not a guarantee.

## Layers

Unit tests:

- Rust crate tests for parser, semantic analysis, code generation, FFI, and CLI behavior.
- Python SDK tests for validation, AST patching, sandboxing, snapshots, rollback, observability, and orchestrator behavior.
- Node SDK tests for validation, AST patching, sandboxing, snapshots, rollback, semantic gate, and security behavior.

Benchmark smoke tests:

- Live in `benchmarks/`.
- Use temp working directories.
- Produce JSON and CSV records.
- Run quickly enough for CI once workflows are added.
- Avoid network, external LLM calls, and unpinned repositories.

Full benchmarks:

- Use pinned repository and task manifests.
- May invoke agent adapters and repeated trials.
- Are manually triggerable or scheduled.
- Must preserve raw output under `benchmarks/results/raw/`.

Failure-injection suite:

- Runs deterministic injected failures without an LLM.
- Covers initial syntax, runtime, broken import, sensitive path, and AST-apply failure cases.
- Covers timeout cases for Python and JavaScript.
- Records `failure_type`, `failure_detected`, rollback fields, and final repository corruption state.
- Does not yet cover process crash, command failure, destructive modification, partial patch, or dependency installation failures.

Agent replay suite:

- Runs deterministic file-based patch emission without an external LLM.
- Exercises benchmark agent ingestion, patch metadata, unchecked control application, Aether validation, snapshot, rollback, and Aether application.
- Records the `replay-agent` adapter in benchmark configuration.
- Does not itself measure model quality or provider cost.

Command/provider agent suite:

- Uses `benchmarks/agents/command_agent.py` to run an external provider command.
- Includes `benchmarks/agents/openai_provider.py` for OpenAI Responses API runs when credentials and model configuration are provided.
- Includes `benchmarks/agents/gemini_provider.py` for Gemini `generateContent` runs when credentials and model configuration are provided.
- Records retry count, adapter latency, token usage, tool calls, model, and cost when the provider command reports them.
- Keeps live/provider calls out of default smoke runs.

External repository suite:

- Supports local worktree fixtures by default.
- Supports external git manifests only when `--allow-network-repos` is explicitly passed.
- External git manifests must use immutable commit SHAs.

## M1/M2 Correctness Suite

The initial correctness suite validates the benchmark harness and a small set of implemented Python behaviors rather than making broad claims about Aether. It checks:

- Control mode can execute a direct deterministic edit in a temp project.
- Aether mode can validate, snapshot, apply, and commit a deterministic Python patch.
- Aether rejects selected invalid Python patches.
- Aether rolls back selected schema-valid Python patches that fail during AST application.
- JavaScript AST transformations run through the Node/Recast adapter for modify, add, remove, and replace-block cases.
- JavaScript Aether benchmark cases now use validation plus snapshot-backed rollback through the Node benchmark adapter.
- Result records include commit SHA, environment metadata, timing, task success, syntax/runtime flags, rollback fields, and error type.
- JSON and CSV output are both generated.
- Summary output includes task success rate plus correctness-oriented rates where applicable.
- Failure-injection summary output includes failure detection rate where applicable.
- Agent replay tasks run through the same Aether path after a deterministic adapter emits patch JSON.
- `benchmarks/analysis/summarize.py` computes N, mean, median, standard deviation, minimum, and maximum for numeric fields from raw JSON.

Current limitation: JavaScript benchmark cases do not yet exercise snapshot-backed rollback because the benchmark adapter calls the Node AST engine directly.

## Metric Definitions

Transformation success rate:

```text
successful transformations / attempted transformations
```

Invalid patch detection rate:

```text
invalid patches rejected / invalid patches submitted
```

Snapshot integrity rate:

```text
snapshots restorable to expected state / snapshot restore attempts
```

Rollback success rate:

```text
successful rollbacks / rollback attempts
```

False acceptance rate:

```text
bad patches accepted / bad patches generated
```

These metrics only describe the tested task distribution and configuration.

## Control vs Aether Design

The central experiment should compare:

```text
control: agent -> repository
aether:  agent -> Aether -> repository
```

Hold constant:

- model
- prompt
- temperature
- task
- starting commit
- environment
- timeout
- number of attempts

The presence of Aether should be the primary independent variable.

## CI Plan

When CI is added, every pull request should run:

- Rust unit tests.
- Python SDK unit tests.
- Node SDK unit tests through Jest or an updated `npm test` script.
- Benchmark smoke suite.

Full agent and real-repository benchmarks should not block pull requests.
