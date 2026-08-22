# Aether Benchmarks

This directory contains the reproducible benchmark program introduced in Milestone M1.

The runner is intentionally evidence-first. It verifies benchmark infrastructure, deterministic correctness tasks, injected failures, replay-agent patch ingestion, command/provider ingestion, local real-repository fixtures, and snapshot-backed Python/JavaScript Aether paths. It does not make broad claims about live LLM-agent performance, safety, or cost from a single provider smoke run.

## Quick Start

From the repository root:

```bash
python benchmarks/run.py --suite correctness --mode both --trials 1
```

Execution modes:

- `control`: direct baseline edit/application.
- `state`: state-transition fast path. It applies the generated structured patch through the AST/state engine and skips Aether validation, snapshots, and rollback. Use it for raw speed/cost experiments when the caller accepts reduced safety.
- `aether`: guarded path with validation, snapshot, apply, and rollback metadata.
- `hybrid`: threshold policy. It uses `state` when the estimated structured patch output is at least 20% smaller than rewriting the target file, uses `control` for tiny safe edits below that threshold, and uses guarded `aether` for expected safety/failure tasks.
- `both`: runs `control` and `aether`.
- `all-modes`: runs `control`, `state`, `aether`, and `hybrid` where each task supports them.

State-mode examples:

```bash
python benchmarks/run.py --suite agent --mode state --trials 3
python benchmarks/run.py --suite agent --mode hybrid --trials 3
python benchmarks/run.py --suite agent --mode hybrid --hybrid-min-output-savings-pct 10
python benchmarks/run.py --suite all --mode all-modes --trials 1
```

If the global `python` executable is not available or does not have the Python SDK dependencies installed, run with the interpreter used for this repository. The runner only uses `sdk/python/.venv/Lib/site-packages` when that environment matches the active Python major/minor version; stale binary packages are rejected with an actionable error.

Outputs are written to:

```text
benchmarks/results/raw/
benchmarks/results/processed/
```

Summarize a raw run:

```bash
python benchmarks/analysis/summarize.py benchmarks/results/raw/<run>.json
```

Compare the state-transition fast path against control and full Aether:

```bash
python benchmarks/analysis/state_efficiency.py benchmarks/results/raw/<run1>.json benchmarks/results/raw/<run2>.json
```

Summarize offline token estimates for local/replay runs:

```bash
python benchmarks/analysis/token_estimates.py benchmarks/results/raw/<run>.json
```

The runner records `estimated_input_tokens`, `estimated_output_tokens`, `estimated_traditional_output_tokens`, and `token_estimator`. If `tiktoken` is installed, estimates use `tiktoken:cl100k_base`; otherwise they use a clearly labeled regex fallback. These fields are separate from provider-reported `input_tokens` and `output_tokens`.

Summarize hybrid routing decisions:

```bash
python benchmarks/analysis/hybrid_policy.py benchmarks/results/raw/<run>.json
```

Estimate dynamic method selection across full-file, state, guarded Aether, hybrid, and graph-scoped variants:

```bash
python benchmarks/analysis/transition_planner.py benchmarks/results/raw/<run>.json
python benchmarks/analysis/transition_planner.py benchmarks/results/raw/<run>.json --graph-context-savings-pct 80
```

Measure raw-source versus graph-scoped context packets:

```bash
python benchmarks/run_context_ab.py --experiment-id context-ab-local --allow-network-repos
python benchmarks/analysis/context_ab_evidence.py benchmarks/results/raw/context-ab-local.json
```

Calculate conservative evidence maturity across one or more raw runs:

```bash
python benchmarks/analysis/proof_score.py benchmarks/results/raw/<run1>.json benchmarks/results/raw/<run2>.json
```

Show what prevents a raw-result bundle from reaching 100%:

```bash
python benchmarks/analysis/proof_gaps.py benchmarks/results/raw/<run1>.json benchmarks/results/raw/<run2>.json
```

Evaluate the completion gates for Phases 4, 5, and 6:

```bash
python benchmarks/analysis/phase_gates.py \
  --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json \
  --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json \
  --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json
```

Evaluate Phase 7 public-benchmark readiness:

```bash
python benchmarks/analysis/phase7_readiness.py \
  --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json \
  --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json \
  --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json \
  --state-results benchmarks/results/raw/state-fastpath-correctness.json benchmarks/results/raw/state-fastpath-agent-trials3.json benchmarks/results/raw/state-fastpath-realrepo-trials3.json benchmarks/results/raw/state-fastpath-all-smoke.json \
  --hybrid-results benchmarks/results/raw/hybrid-threshold-all-smoke.json benchmarks/results/raw/hybrid-threshold-smoke-v2.json \
  --external-results benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json \
  --proof-results benchmarks/results/raw/live-openrouter-smoke-v3.json benchmarks/results/raw/phase6-expanded-all.json benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json benchmarks/results/raw/phase4-realrepo-done-trials3.json benchmarks/results/raw/phase5-crosslang-done-trials3.json benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json
```

Generate a Phase 7 public benchmark bundle:

```bash
python benchmarks/analysis/phase7_bundle.py \
  --phase4 benchmarks/results/raw/phase4-realrepo-done-trials3.json \
  --phase5 benchmarks/results/raw/phase5-crosslang-done-trials3.json \
  --phase6 benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json \
  --state-results benchmarks/results/raw/state-fastpath-correctness.json benchmarks/results/raw/state-fastpath-agent-trials3.json benchmarks/results/raw/state-fastpath-realrepo-trials3.json benchmarks/results/raw/state-fastpath-all-smoke.json \
  --hybrid-results benchmarks/results/raw/hybrid-threshold-all-smoke.json benchmarks/results/raw/hybrid-threshold-smoke-v2.json \
  --external-results benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json \
  --token-results benchmarks/results/raw/token-estimate-all-smoke.json \
  --proof-results benchmarks/results/raw/live-openrouter-smoke-v3.json benchmarks/results/raw/phase6-expanded-all.json benchmarks/results/raw/phase6-agent-ab-expanded-command-mock-trials3.json benchmarks/results/raw/phase4-realrepo-done-trials3.json benchmarks/results/raw/phase5-crosslang-done-trials3.json benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json
```

## Suites

Implemented now:

- `correctness`: deterministic Python and JavaScript cases for control and Aether modes. Python includes valid transformations, validation rejections, and rollback-on-apply-failure checks. JavaScript currently covers valid AST transformations through the Node/Recast adapter.
- `failure-injection`: deterministic Python and JavaScript injected-failure cases for syntax errors, runtime errors, broken imports, sensitive paths, and AST-apply failures.
- `agent`: deterministic replay-agent cases that emit patch JSON through an adapter before either unchecked control application or Aether validation/snapshot/application.
- `real-repository`: local Aether repository-derived Python and JavaScript tasks copied into isolated temp directories.
- `external-repository`: five immutable GitHub checkouts with matched full-file control, state, Aether, hybrid, behavioral, syntax, and rollback evidence. Requires `--allow-network-repos` on a cold cache.
- `external-agent`: hash-locked patches produced by independent blind Codex subagents for unpublished hidden-behavior tasks on pinned repositories. The command adapter is required.
- `smoke`: alias for `correctness`.
- `all`: runs correctness, failure-injection, replay-agent, and local real-repository tasks.

Agent adapters:

- `replay`: deterministic, no network, no model calls.
- `command`: external command adapter for live/provider integrations. It receives a source-bearing task descriptor on stdin, without the reference patch, and may report `input_tokens`, `output_tokens`, `tool_calls`, `latency_ms`, `cost_usd`, and `model`.

Local command-adapter check:

```bash
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/mock_provider.py
```

Pinned external repository check:

```bash
python -m pip install -e sdk/python tiktoken
cd sdk/node
npm ci
npm run build
cd ../..
python benchmarks/run.py --suite external-repository --mode all-modes --trials 3 --allow-network-repos --experiment-id external-matrix-allmodes-trials3-v3
python benchmarks/analysis/external_efficiency.py benchmarks/results/raw/external-matrix-allmodes-trials3-v3.json --json-output benchmarks/results/public/external_repository_efficiency.json --markdown-output benchmarks/results/public/EXTERNAL_REPOSITORY_REPORT.md
```

The suite pins five repositories at immutable commits: MarkupSafe, Packaging, Requests, escape-string-regexp, and yocto-queue. Ten valid edit tasks run matched control/state/Aether/hybrid conditions. Two guarded fault tasks exercise Python and JavaScript rollback in Aether and hybrid modes. Checkouts are cached by repository and commit under `.tmp/benchmark-repositories`; checkout time is tracked separately from edit execution.

Blind external-agent replay:

```bash
python benchmarks/agents/export_blind_packets.py --manifest benchmarks/tasks/external_agent_unseen.json --output-dir .tmp/blind-external-packets --trials 3
python benchmarks/run.py --suite external-agent --mode all-modes --trials 3 --allow-network-repos --experiment-id blind-external-agent-trials3-v1 --agent-adapter command --agent-command python benchmarks/agents/blind_external_provider.py
python benchmarks/analysis/blind_agent_evidence.py benchmarks/results/raw/blind-external-agent-trials3-v1.json --json-output benchmarks/results/public/blind_external_agent_evidence.json --markdown-output benchmarks/results/public/BLIND_EXTERNAL_AGENT_REPORT.md
```

Blind packets contain the task description, visible source, target file, and public patch contract. Test commands, expected values, acceptance fields, and reference patches are withheld. Stored outputs are SHA-256 locked to the exact descriptor and replayed without oracle-assisted normalization. This is prompt-level blinding rather than OS-enforced isolation; fresh tasks are required for every future generation study.

OpenAI provider run:

```bash
set OPENAI_API_KEY=<key>
set AETHER_OPENAI_MODEL=<model>
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/openai_provider.py
```

Gemini provider run:

```bash
set GEMINI_API_KEY=<key>
set AETHER_GEMINI_MODEL=gemini-3.5-flash
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/gemini_provider.py
```

OpenRouter provider run:

```bash
set OPENROUTER_API_KEY=<key>
set AETHER_OPENROUTER_MODEL=<pinned-free-model-or-openrouter/free>
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/openrouter_provider.py
```

Prefer a pinned `:free` model for evidence runs. Use `openrouter/free` for smoke checks where model routing variance is acceptable.

For faster JavaScript Aether benchmark runs, build the Node SDK before running the benchmark:

```bash
cd sdk/node
npm run build
```

The Node benchmark adapter uses compiled `dist/` output when it exists and falls back to `ts-node/register` otherwise.

Do not add hand-written benchmark results.

## Phase 7 Improvement Path

Research-aligned upgrades for stronger public results:

- Version every benchmark dataset and bump the version when tasks or grading change.
- Keep objective pass/fail checks as the primary score. Use model or human judging only as a secondary rubric when automated tests cannot grade the task.
- Publish raw JSON, processed CSV, exact commands, commit SHA, OS/runtime metadata, and known limitations together.
- Expand the pinned external dataset beyond the current five repositories and twelve tasks.
- Increase live-provider repetitions only when cost/quota allows, and report provider failures separately from Aether failures.
- Keep `state` and `aether` results separate: `state` measures raw transition efficiency, while `aether` measures guarded agent safety.
- Use `hybrid` to model a practical product default: traditional/full generation for very small first drafts, state transitions for token-efficient focused edits, and full Aether for safety-sensitive failures.

## Result Policy

- Raw JSON is the source of truth.
- CSV is a convenience export.
- Every number in future evidence reports should be traceable to raw JSON.
- Missing measurements should be recorded as `null`, not invented.
- Offline token estimates are useful for local comparisons, but only provider-reported `input_tokens` and `output_tokens` should be treated as live billing telemetry.
- External efficiency records track full-rewrite control input/output, structured-patch input/output, emitted bytes, repository setup, raw apply time, verification time, and edit-to-verified time. They never fabricate model generation latency.
- JavaScript Aether benchmark runs now use validation plus `SnapshotStore` rollback through the Node benchmark adapter.
- Correctness summaries include transformation success rate, invalid-patch detection rate, false acceptance rate, rollback success rate, and expected rollback detection rate where the task mix supports them.
- Failure-injection summaries include `failure_detection_rate` where the task mix supports it.
- Replay-agent summaries cover deterministic patch ingestion only. Token usage, model cost, and live agent behavior remain `null` unless a command/provider adapter reports them.
- Proof-score summaries are conservative evidence-maturity reports. They reward repeated evaluable pairs, provider availability, live token telemetry, safety coverage, and real-repository coverage; they do not treat one perfect smoke run as universal proof.
- The current three-trial external matrix passed 132/132 records across five pinned repositories, twelve tasks, Python and JavaScript, and four execution modes where supported.
- Phase-gate summaries define "done" for the current reproducible benchmark scope. They do not claim public Phase 7 replication or broad live-provider generality.
- State-mode summaries measure the raw transition engine without Aether's safety envelope. A faster state run is not equivalent to full Aether safety.
