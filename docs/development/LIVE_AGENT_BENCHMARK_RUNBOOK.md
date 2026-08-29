# Live Agent Benchmark Runbook

This runbook describes how to collect larger live-agent evidence without
mixing provider failures, Aether failures, and benchmark harness failures.

## Goal

Measure whether Aether helps real coding agents:

- read less context;
- write fewer output tokens;
- preserve or improve task success;
- reject unsafe patches;
- keep latency/cost tradeoffs explicit.

## Recommended Matrix

Run at least:

```text
3 providers or models
3 trials each
correctness + agent + real-repository suites
control + state + aether + hybrid modes
```

Minimum useful smoke:

```bash
python benchmarks/run.py --suite agent --mode all-modes --trials 3 \
  --agent-adapter command \
  --agent-command "python benchmarks/agents/openrouter_provider.py" \
  --experiment-id live-openrouter-agent-allmodes-trials3
```

OpenRouter example:

```bash
set OPENROUTER_API_KEY=<key>
set AETHER_OPENROUTER_MODEL=<pinned-model>
python benchmarks/run.py --suite agent --mode all-modes --trials 3 \
  --agent-adapter command \
  --agent-command "python benchmarks/agents/openrouter_provider.py" \
  --experiment-id live-openrouter-agent-allmodes-trials3
```

Gemini example:

```bash
set GEMINI_API_KEY=<key>
set AETHER_GEMINI_MODEL=<model>
python benchmarks/run.py --suite agent --mode all-modes --trials 3 \
  --agent-adapter command \
  --agent-command "python benchmarks/agents/gemini_provider.py" \
  --experiment-id live-gemini-agent-allmodes-trials3
```

Local/Ollama agents should report the same metadata fields through a command
adapter:

```json
{
  "patch": {},
  "input_tokens": 1234,
  "output_tokens": 120,
  "latency_ms": 850,
  "cost_usd": 0.0,
  "model": "local-model-name"
}
```

## Required Reporting

For every run, publish:

- raw JSON under `benchmarks/results/raw/`;
- processed CSV under `benchmarks/results/processed/`;
- provider/model name;
- provider availability rate;
- success rates by mode;
- output-token savings;
- input-token/context savings when available;
- latency overhead/savings;
- cost overhead/savings;
- invalid-patch detection and rollback success;
- failures that were provider refusals/timeouts rather than Aether failures.

## Analysis Commands

```bash
python benchmarks/analysis/efficiency.py benchmarks/results/raw/<run>.json
python benchmarks/analysis/token_estimates.py benchmarks/results/raw/<run>.json
python benchmarks/analysis/proof_score.py benchmarks/results/raw/<run>.json
```

## Guardrails

- Do not compare a live provider run against a deterministic replay run as if
  they measure the same thing.
- Do not claim cost savings unless provider telemetry or a documented price
  model is available.
- Keep `state` and `aether` separate: state measures speed/token efficiency;
  Aether measures guarded safety.
- Record skipped or unavailable provider calls instead of deleting them.
