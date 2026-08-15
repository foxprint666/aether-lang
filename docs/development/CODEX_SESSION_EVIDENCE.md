# Codex Session Evidence

Date: 2026-08-15

This document records observed outcomes from a live Codex coding-agent session working on Aether. It now includes a small provider-backed Gemini smoke run, but that run is still not a substitute for a repeated statistical benchmark over realistic repositories.

## What This Session Demonstrated

Observed live-agent outcomes:

- Built a reproducible benchmark scaffold with raw JSON and CSV outputs.
- Added deterministic correctness, failure-injection, replay-agent, command-agent, and local real-repository benchmark paths.
- Added retry, token, tool-call, latency, cost, and model fields to benchmark records.
- Added a mock provider command and an OpenAI Responses API provider wrapper.
- Added a Gemini `generateContent` provider wrapper and ran a live Gemini agent smoke benchmark.
- Added matched replay-agent `control` vs `aether` benchmark runs.
- Added snapshot-backed JavaScript Aether benchmark rollback through the Node adapter.
- Added JavaScript invalid-patch and rollback benchmark cases.
- Added Python and JavaScript timeout failure-injection cases.
- Added local real-repository benchmark tasks copied from the Aether worktree.
- Found a real Python AST bug: `update_import` could insert imports before `from __future__ import annotations`.
- Fixed that bug in `sdk/python/ai_runtime/ast/engine.py`.
- Added a Python unit regression for the import-order issue.
- Restored the real-repository import benchmark that originally exposed the bug.
- Added benchmark smoke CI and broader Rust/Python/Node quality CI.
- Rewrote evidence documentation to separate observed results from unproven claims.
- Fixed the Gemini adapter after live failures exposed an incompatible structured-output payload and missing provider-to-Aether patch normalization.
- Fixed benchmark timing semantics so command-agent control and Aether records both keep provider generation latency separate from local execution time.

## Verification From This Session

Latest local verification:

- Live Gemini command-agent suite: 5/5 records passed with 2,304 input tokens, 256 output tokens, and 27,227.455 ms total provider latency.
- Corrected live Gemini efficiency: matched normal tasks had 100% success in both modes, 0% token delta, and 58.073% lower local execution time for Aether mode after correcting legacy control timing.
- Full benchmark suite: 46/46 records passed.
- Failure-injection suite: 15/15 records passed.
- Command-agent mock provider suite: 5/5 records passed.
- Real-repository suite: 2/2 records passed.
- Python focused tests: 17 passed.
- Node focused tests: 14 passed.
- Rust `ae-codegen` tests: 8 passed.

## What This Session Does Prove

In this live agent session, Aether development benefited from an AI coding agent in concrete ways:

- A real implementation defect was discovered through benchmark work.
- The defect was fixed and locked with unit plus benchmark regression coverage.
- The benchmark system expanded from synthetic-only validation toward agent, provider, failure, CI, and real-repository infrastructure.
- The repository ended with reproducible commands that can be rerun by a human or CI.
- A live Gemini provider path can produce usable Aether patches through the command-agent adapter for the current small smoke suite.

## What This Session Does Not Prove

This session does not prove:

- Billed token savings across real workloads, because only a small Gemini smoke suite has exact provider token metadata.
- Cost savings, because no Gemini per-token cost rates were configured for this run.
- Statistical live-agent success rates, because this is one provider-backed smoke run, not a repeated matched experiment.
- General real-repository performance across external pinned repositories, because only local Aether-derived real-repository tasks were executed.

## Next Empirical Step

Run repeated provider-backed benchmarks against pinned external repositories:

```bash
python benchmarks/run.py --suite real-repository --mode both --allow-network-repos --trials 3
python benchmarks/run.py --suite agent --mode both --trials 5 --agent-adapter command --agent-command python benchmarks/agents/gemini_provider.py
```

That produces broader raw JSON/CSV evidence for success rate, rollback behavior, tokens, latency, retries, and configured cost estimates.
