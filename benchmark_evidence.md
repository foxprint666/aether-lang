# Aether Benchmark Evidence

This report summarizes the reproducible benchmark infrastructure now present in `benchmarks/`. It replaces earlier informal token-savings notes with evidence language that distinguishes observed results from claims that still require live provider data.

## What Was Observed

Latest local verification during this development session:

| Suite | Mode | Records | Result |
|---|---:|---:|---|
| `correctness` | `both` | 24 | 24 passed |
| `failure-injection` | `both` | 15 | 15 passed |
| `agent` | `both` | 5 | 5 passed |
| `agent` with `command` adapter and mock provider | `both` | 5 | 5 passed |
| `agent` with `command` adapter and Gemini provider | `both` | 5 | 5 passed |
| `real-repository` | `aether` | 6 | 6 passed |
| `external-repository` | `all-modes`, three trials | 132 | 132 passed |
| `external-agent` blind generation | `all-modes`, three trials | 96 | 64 passed (16/24 generated patches) |
| `paired blind agent generation` | Aether patch vs full file, three trials | 42 | Aether patches 10/21; full files 17/21 |
| `transition planner projection` | external matrix, graph input-savings model | 30 task/trial groups | 28.964% token savings measured-routing; 74.479% with synthetic 80% graph-context savings |
| `context A/B` | raw source vs graph-scoped packets | 7 | 59.245% input-token savings; 100% target-symbol hit rate |
| `all` | `both` | 50 | 50 passed |
| `all` | `all-modes` | 85 | 85 passed |

Additional checks:

- Python focused tests: 17 passed.
- Node AST tests: 8 passed.
- Current focused verification also passed Python AST plus rollback tests: 17 passed; Node AST plus rollback tests: 14 passed; Rust `ae-codegen`: 8 passed.
- A live Codex coding-agent session produced the infrastructure and bug fix summarized in `docs/development/CODEX_SESSION_EVIDENCE.md`.
- Benchmark analyzer ran successfully on combined benchmark output in the previous slice.

Latest live Gemini smoke run:

- Experiment: `live-gemini-agent-check-v4`.
- Provider/model: Gemini `gemini-3.5-flash` through `benchmarks/agents/gemini_provider.py`.
- Result: 5/5 agent records passed, covering Python modify-function control, Python modify-function Aether, JavaScript modify-function control, JavaScript modify-function Aether, and Aether rejection of a sensitive-path patch.
- Token metadata: 2,304 input tokens and 256 output tokens across five calls.
- Latency metadata: 27,227.455 ms total provider latency, 5,445.491 ms average provider latency.
- Matched normal-task efficiency after correcting legacy control-agent timing: 100% success in both modes, 0% token delta, and 58.073% lower local execution time for Aether mode. Provider latency was 60.499% higher in Aether records, but those were separate live Gemini calls and should be treated as provider response variance rather than Aether mechanism overhead.
- Cost metadata: not estimated because no Gemini per-token cost rates were configured locally.
- Earlier live attempts exposed and fixed two adapter issues: an incompatible Gemini structured-output request shape and insufficient provider-to-Aether patch normalization.
- A benchmark timing issue was also found and fixed: command-agent control records previously included provider generation time in `execution_time_ms`, while Aether records did not. New records tag `agent_generation_excluded_from_execution_time=true`, and `benchmarks/analysis/efficiency.py` can correct old live records with `--legacy-control-agent-timing`.
- A corrected-timing three-trial Gemini run was attempted as `live-gemini-corrected-timing-trials3`. The first trial produced tested records; later trials hit Gemini free-tier daily quota (`429 RESOURCE_EXHAUSTED`). The benchmark metrics now distinguish provider/quota failures from actual tested unsafe-patch false acceptances.
- The Gemini provider now retries transient 429/5xx responses with parsed retry delays, but fails fast on non-retryable daily quota exhaustion.
- Provider failures now have structured fields for error type, HTTP status, retryability, and quota exhaustion. The efficiency analyzer reports both total matched pairs and evaluable pairs so provider availability is separated from Aether/model success.
- OpenRouter provider support is implemented through `benchmarks/agents/openrouter_provider.py` using the OpenAI-compatible chat-completions endpoint. For reproducible free evidence, use a pinned `:free` model rather than `openrouter/free`.
- The first OpenRouter smoke attempt, `live-openrouter-smoke`, reached OpenRouter but failed with `401 Missing Authentication header`, so no OpenRouter patch was evaluated. After replacing the local key and sanitizing provider metadata, `live-openrouter-smoke-v3` passed 5/5 records.
- Latest OpenRouter smoke, `live-openrouter-smoke-v3`: 2,300 input tokens, 4,356 output tokens, 118,315.635 ms total provider latency, 23,663.127 ms average provider latency, 100% provider availability for matched pairs, 100% control success, 100% Aether success, 100% invalid-patch detection, and 0% false acceptance. Cost metadata is still null because free-model cost rates were not configured.
- OpenRouter matched normal-task efficiency in `live-openrouter-smoke-v3`: Aether used 9.295% more input tokens but 83.259% fewer output tokens, had 88.975% lower provider latency in this small run, and 21.129% lower local execution time. Treat provider-latency differences cautiously because `openrouter/free` may route to varying free models.
- A Codex subagent-simulated provider fallback, `subagent-simulated-provider-smoke-v2`, produced provider-like patches from task descriptors without using the reference manifest patches. After schema-safe normalization, it passed 5/5 records. This is useful fallback agent evidence, but it is not external provider token/cost telemetry.
- A parallel Codex subagent trial, `parallel-subagent-agent-trials3`, used three independent subagents to produce Aether patches from task descriptors/source without reference manifest patches. Replayed through the command-agent benchmark harness, it passed 15/15 records: 6/6 matched valid control/Aether programming records, 3/3 Aether invalid sensitive-path detections, 0% false acceptance, and 100% provider availability for the local agent replay path.
- Expanded Phase 4 local real-repository coverage, `phase4-realrepo-expanded-v2`, passed 6/6 records across real Aether Python and JavaScript files copied into isolated temp directories.
- Expanded full local benchmark, `phase4-expanded-all`, passed 50/50 records across deterministic correctness, failure injection, replay-agent, and local real-repository tasks.
- Repeated expanded full local benchmark, `phase4-expanded-all-trials3`, passed 150/150 records. This gives a 100% pass rate for the expanded local tested scope.
- Conservative combined proof scoring with live API, parallel subagent, subagent fallback, and expanded offline evidence reached 85.615% on the single expanded run and 83.224% when the three-trial expanded run is used. The parallel subagent lane itself is 100% for the scoped agent-use benchmark. The overall proof score remains lower because live token telemetry is still limited and local execution-time overhead is now measured more clearly.
- Node benchmark adapter optimization now prefers compiled `sdk/node/dist` output over `ts-node/register` when available. In `optimized-node-dist-all`, the expanded full local suite passed 50/50 records, JavaScript Aether mean execution time dropped from 561.329 ms to 295.153 ms (47.415% lower), and matched local Aether mean execution time dropped from 335.855 ms to 202.396 ms (39.737% lower).
- Conservative combined proof scoring with the optimized Node adapter reached 86.213%. The score still does not reach 100% because the direct-edit local control baseline is much faster on tiny files and because live token telemetry remains limited.
- Phase 5 cross-language parity was strengthened in `phase5-crosslang-failure-parity`: failure-injection passed 18/18 records, with Python and JavaScript both covering syntax error, runtime error, broken import, timeout, and sensitive-path rejection where applicable.
- Expanded cross-language full run `phase5-crosslang-all` passed 53/53 records. JavaScript coverage increased to 25 records, including 16 Aether records; conservative combined proof scoring with this run reached 86.005%.
- Phase 6 A/B agent coverage was expanded from two matched programming tasks to seven matched programming tasks plus two invalid safety tasks across Python and JavaScript. The expanded replay A/B run, `phase6-agent-ab-expanded-replay-trials3`, passed 48/48 records over three trials.
- The command-backed mock provider A/B run, `phase6-agent-ab-expanded-command-mock-trials3`, also passed 48/48 records over three trials from task descriptors without reference patches. It produced 21 matched control/Aether programming pairs, 6/6 invalid sensitive-path detections, and 0% false acceptance.
- The expanded full local run, `phase6-expanded-all`, passed 64/64 records. Conservative combined proof scoring with live/provider, parallel-subagent, Phase 6 local, and Phase 6 command A/B evidence reached 85.172%. The scorer now excludes local mock/replay/subagent token fields from live-token confidence, so live token pairs remain 10.
- Phase completion gates now mark Phases 4, 5, and 6 done for the current reproducible benchmark scope. `phase4-realrepo-done-trials3` passed 18/18 real-repository records over three trials, `phase5-crosslang-done-trials3` passed 54/54 cross-language failure-injection records over three trials, and `phase6-agent-ab-expanded-command-mock-trials3` passed 48/48 expanded command-backed A/B agent records over three trials. `benchmarks/analysis/phase_gates.py` reports `all_done=true` for these three runs.
- State-transition fast-path mode is now available through `--mode state` and `--mode all-modes`. It applies generated structured patches directly through the AST/state transition engine, skipping Aether validation, snapshots, and rollback for users who want a raw speed/cost path and accept lower safety.
- State fast-path evidence passed 137/137 records across correctness, expanded replay-agent, and local real-repository runs. On 47 matched state/Aether records, state mode averaged 123.459 ms and full Aether averaged 176.240 ms, so state mode was 29.948% faster than full Aether while preserving 100% task success in this scoped run. On the all-suite smoke, `state-fastpath-all-smoke` passed 85/85 records; state averaged 122.875 ms versus 178.408 ms for full Aether on 21 matched state/Aether records, a 31.127% speed improvement. Against direct control, state still had overhead on tiny local fixtures because patch generation, structured AST application, and Node subprocess startup dominate very small edits.
- Offline token-estimate fields are now recorded separately from live provider token telemetry: `estimated_input_tokens`, `estimated_output_tokens`, `estimated_traditional_output_tokens`, and `token_estimator`. `tiktoken:cl100k_base` is used when available; otherwise the runner records `heuristic:regex-v1`. In `token-estimate-all-smoke`, 85/85 records passed and 69 patch records received estimates. Overall patch output was 13.069% smaller than a full target-file rewrite baseline, while state-mode patch output was 40.942% smaller. JavaScript tiny-file fixtures were negative because the structured patch JSON is larger than the very small source files; larger Python real-repository fixtures showed stronger savings.
- Hybrid mode is now available through `--mode hybrid`. The default policy uses state transitions only when the estimated structured patch output is at least 20% smaller than a full target-file rewrite, uses control/direct editing for tiny safe edits below the threshold, and uses guarded Aether for expected safety/failure cases. In `hybrid-threshold-all-smoke`, hybrid mode passed 43/43 records. It selected control for 17 tiny/safe records, state for 6 records that cleared the token-savings threshold, and Aether for 20 safety/failure records. The 6 state-selected records averaged 77.075% estimated output-token savings.
- Expanded external evidence, `external-matrix-allmodes-trials3-v3`, passed 132/132 records across five immutable GitHub repositories, twelve tasks, Python and JavaScript, and three trials against harness commit `a5d95ce5137e5f404873703f3c36c4f0ff42c6e8`. The matrix contains 96 behavior records, 24 syntax records, and 12 guarded rollback records. Hybrid versus full-file control saved 75.014% estimated output tokens, 28.842% estimated total tokens, and 79.423% emitted bytes while adding 41.323 ms mean edit-to-verified time; its bootstrap 95% interval was 15.503 to 71.031 ms. These are `tiktoken:cl100k_base` offline estimates, not provider billing telemetry.
- Blind external-agent evidence, `blind-external-agent-trials3-v1`, evaluated 24 independently generated patches over eight previously unpublished tasks in five pinned repositories. The agents received source-only packets without tests, expected output, or reference patches. Sixteen of 24 patches passed hidden behavior tests (`66.667%`); replaying each patch through control, state, Aether, and hybrid produced 64/96 successful records with identical outcomes in every mode. All 24 prompt and patch hash groups matched across modes, all 96 records were marked blind, and zero records reported oracle use.
- In that blind run, structured patches used 78.231% fewer estimated output tokens and 82.802% fewer emitted bytes than full-file output. Mean edit-to-verified time was 228.433 ms for direct control, 224.467 ms for state, 339.777 ms for Aether, and 194.897 ms for hybrid. Thus Aether was 48.743% slower than control, state was 1.736% faster, and hybrid was 14.681% faster in this small local run. These timings exclude live model generation and should not be generalized beyond this workload.
- The eight failed generations were preserved: four contained malformed Python body indentation and four failed hidden behavior. During calibration, valid agent payloads also exposed and led to fixes for Python common-indentation handling and JavaScript private-field parsing. Calibration outputs are excluded from the final result.
- Paired blind agent-generation evidence, `paired-blind-agent-trials3-v1`, evaluated matched Aether-patch and full-file outputs over seven hidden-test tasks, four pinned repositories, and three trials. Original revisions passed zero hidden checks, so all tasks required a real change. Aether patches succeeded 10/21 (`47.619%`), while full-file outputs succeeded 17/21 (`80.952%`). Full-file-only wins were 7, Aether-only wins were 0, exact McNemar p was `0.015625`, and the task-clustered bootstrap interval for Aether minus full-file success was `-47.619` to `-19.048` percentage points.
- In the paired blind run, Aether patch outputs used 3,999 estimated tokens versus 22,658 for full files: `82.351%` output-token savings and `86.118%` emitted-byte savings. Mean local application time was 188.996 ms for Aether patch application versus 0.721 ms for full-file overwrite; mean verification time was similar at 52.517 ms versus 54.050 ms. Generation used fresh stateless Codex subagents with prompt-enforced source-only restrictions, not OS-enforced denial.
- Transition planner analysis is now implemented in `benchmarks/analysis/transition_planner.py`. On the external matrix, a token-first dynamic planner selected full-file generation for 12/30 task-trial groups, state transitions for 7/30, guarded Aether for 8/30, and existing hybrid-state for 3/30. It preserved 100% success and reduced estimated total tokens from 36,069 to 25,622 (`28.964%` savings), while increasing local edit-to-verified time from 2,165.703 ms to 4,183.745 ms.
- A graph-context projection using a synthetic 80% input-token reduction selected full-file generation for 3/30 groups, graph-scoped state transitions for 15/30, and graph-scoped guarded Aether for 12/30. It preserved 100% success and reduced estimated total tokens from 36,069 to 9,205 (`74.479%` savings), while increasing local edit-to-verified time to 5,729.297 ms. This is a projection from existing records, not a measured Graphify-integrated run.
- Context A/B evidence, `context-ab-paired-v1`, measured raw full-source packets versus lightweight graph-scoped packets across the seven paired blind tasks. Raw context used 9,589 estimated tokens; graph-scoped context used 3,908, a `59.245%` input-token reduction. Target-symbol hit rate was `100%`, mean graph build time was 1.013 ms, and byte savings were `63.932%`. This measures what the agent reads, not whether a live agent can solve the task from the graph packet.

## What This Proves So Far

In the tested configuration:

- The benchmark runner produces raw JSON and CSV records with commit/environment metadata.
- Python and JavaScript valid AST transformations pass deterministic behavior checks.
- Python and JavaScript invalid patches are rejected by Aether validation.
- Python and JavaScript schema-valid AST apply failures trigger snapshot-backed rollback in Aether mode.
- Python and JavaScript failure-injection suites now cover matching syntax, runtime, broken-import, timeout, and sensitive-path classes for the benchmark-supported paths.
- Replay-agent tasks can run in matched `control` and `aether` modes from the same patch-producing adapter.
- The A/B agent benchmark now covers multiple operation types in matched control/Aether mode: Python modify-function, import update, block replacement, JavaScript modify-function, add-function, remove-function, and block replacement.
- Command-agent tasks can ingest provider-shaped usage metadata: model, input/output tokens, tool calls, attempts, latency, and cost.
- Gemini-backed command-agent tasks can run through the live provider path with retry, token, latency, model, and patch-size metadata.
- Command/provider task descriptors no longer include the reference patch, so live provider runs are not handed the answer key.
- Users can choose a raw state-transition path when they need the base mechanism without the full validation/snapshot/rollback envelope.
- Users can choose a hybrid threshold policy that routes tiny first-draft/safe edits to control, token-efficient focused edits to state, and safety-sensitive edits to full Aether.
- A benchmark-only transition planner can now estimate dynamic method selection across full-file, state, guarded Aether, existing hybrid, and graph-scoped variants with configurable graph context savings and latency weighting.
- Lightweight graph-scoped context packets can reduce visible input context on the paired external tasks while still selecting the named target symbol.
- Local real-repository fixtures copied from this repository can be patched and verified in isolated temp projects across Python and JavaScript files.
- The expanded local benchmark can be repeated over three trials with 100% task success, 100% Aether invalid-patch detection, 0% false acceptance, and 100% rollback success for rollback-triggering cases.
- The Phase 4/5/6 acceptance gates are machine-checkable and currently pass for the local reproducible benchmark scope.
- External git repository manifests, immutable checkout caching, full-file control baselines, state/Aether/hybrid matching, and per-repository efficiency reporting are supported behind `--allow-network-repos`. The current five-repository matrix passed 132/132; broader project and task distributions remain future work.
- Source-only blind-agent packets, hash-locked generated-patch replay, hidden tests, and a manual CI workflow now provide leak-resistant unseen-task evidence. The first final run passed 16/24 independent generations without answer-key normalization.
- A matched blind full-file-generation control arm now exists. On the first 7-task run, full-file generation was more successful than Aether patch generation, while Aether retained large output-token savings. This proves the token-efficiency mechanism but shows agent-facing patch generation needs more reliability work before claiming success-rate gains.
- The result schema now includes retry, token, tool-call, latency, and cost fields for live/provider adapters.
- The result schema now also supports offline estimated-token fields without mixing them into provider-reported billing telemetry.
- Python import insertion now preserves module docstrings and `from __future__` ordering in the tested AST and real-repository cases.
- In this Codex session, an AI coding agent found and fixed a real implementation bug, then added regression coverage and benchmark verification.

## What This Does Not Prove Yet

These claims remain outside the Phase 4/5/6 local completion gates and should be handled in Phase 7/public replication:

- Whether Aether makes agents more successful than a matched full-file-generation control. The first paired blind run showed the opposite on this small task set: full-file output was more successful, while Aether patch output was much smaller.
- Whether the observed external token savings hold beyond the current five repositories and ten valid edit tasks.
- Whether the expanded A/B agent result holds for external live providers rather than local replay/mock providers.
- Whether the measured external latency tradeoff remains favorable with real model generation, retries, and provider billing.
- Whether the benchmark adapter/runtime overhead can be reduced enough for Aether to beat direct string edits on very small local files.
- Whether state-transition mode beats direct control on larger real edits and live-agent loops where structured edit correctness may reduce retries.
- Whether JavaScript rollback safety is broadly comparable to Python across larger external repositories and still more failure classes.
- Whether broader external git repository benchmarks remain stable across CI/network environments.
- Whether blind generation results reproduce under an OS-enforced agent sandbox; the current source-only restriction was enforced by prompts and independently checked packet contents.
- Whether better patch schemas, examples, constrained decoding, or repair loops can close the paired blind success gap without giving back the observed output-token savings.
- Whether a real Graphify-style code graph, measured inside the benchmark harness, delivers the projected input-token savings once graph build/update time and query accuracy are included.
- Whether live agents can preserve or improve correctness when given graph-scoped packets instead of raw full-source packets.

## Issue Found And Fixed During Real-Repo Smoke Work

A real-repository task attempting to add a Python import to `sdk/python/ai_runtime/sandbox_runner.py` exposed an import-order issue: the previous Python import insertion path could place imports before `from __future__ import annotations`, causing a syntax error.

That issue is now fixed in the Python AST engine and covered by both a unit regression and a real-repository benchmark task.

## How To Reproduce

From the repository root, after installing Python SDK dependencies and Node dependencies:

```bash
python benchmarks/run.py --suite correctness --mode both --trials 1
python benchmarks/run.py --suite agent --mode both --trials 1
python benchmarks/run.py --suite real-repository --mode aether --trials 1
```

Use a live/provider command adapter when available:

```bash
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command <your-agent-command>
```

OpenAI provider command support is available through `benchmarks/agents/openai_provider.py`; it requires `OPENAI_API_KEY` and `AETHER_OPENAI_MODEL`.

Gemini provider command support is available through `benchmarks/agents/gemini_provider.py`; it requires `GEMINI_API_KEY` and `AETHER_GEMINI_MODEL`.

Raw results are written to `benchmarks/results/raw/`; processed CSV files are written to `benchmarks/results/processed/`.
