# Benchmark Methodology

## Scope

The benchmark program measures observed behavior in specific, reproducible configurations. It must not be used to claim universal safety, correctness, or performance.

## Experimental Conditions

Control:

```text
agent or deterministic edit -> repository
```

Aether:

```text
agent or deterministic patch -> Aether -> repository
```

For agent benchmarks, keep these constant across conditions:

- model
- prompt
- temperature
- task
- starting commit
- environment
- timeout
- attempt count

The intended primary variable is the presence of Aether.

## Result Recording

Raw result records are written to:

```text
benchmarks/results/raw/
```

Processed CSV summaries are written to:

```text
benchmarks/results/processed/
```

Raw JSON can be summarized with:

```bash
python benchmarks/analysis/summarize.py benchmarks/results/raw/<run>.json
```

Every result should include:

- experiment id
- timestamp
- commit SHA
- task id
- repository
- language
- agent/model metadata when available
- configuration
- patch size when available
- token/tool-call counts when measured
- agent retry count, adapter latency, and cost when measured
- execution and validation timing
- repository setup, verification, edit-to-verified, and total task timing
- source, repository, emitted-output, and full-rewrite byte sizes
- test outcomes
- syntax/runtime/validation flags
- rollback flags
- task success
- repository corruption flag
- error type
- provider error type, HTTP status, retryability, and quota-exhaustion flags
- task category when available
- failure type and detection status for injected-failure tasks
- repository manifest for real-repository tasks

Unknown measurements should be `null`, not estimated.

## Live Provider Adapter Contract

The command-backed adapter expects the provider command to read a task descriptor from stdin and write JSON to stdout. For live/provider runs, the descriptor includes task description, source file path, source content, test command, timeout, and schema guidance; it does not include the reference manifest patch. The replay adapter is the only adapter that receives the reference patch by design.

The provider output may be either a patch object or an envelope:

```json
{
  "patch": {
    "schema_version": "1.0",
    "patch_id": "00000000-0000-4000-8000-000000000000",
    "action": "modify_function",
    "target": { "file": "app.py", "symbol": "total", "symbol_type": "function" },
    "changes": { "operation": "replace_body", "payload": "return 42\n" },
    "metadata": { "agent_id": "provider-agent", "model": "model-name" }
  },
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50,
    "tool_calls": 1,
    "latency_ms": 1200,
    "cost_usd": 0.001,
    "model": "model-name"
  }
}
```

Run it with:

```bash
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command <your-agent-command>
```

For the included OpenAI provider wrapper:

```bash
set OPENAI_API_KEY=<key>
set AETHER_OPENAI_MODEL=<model>
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/openai_provider.py
```

The wrapper uses OpenAI's Responses API text format configuration for structured JSON output and still relies on local Aether validation before a patch is accepted.

For the included Gemini provider wrapper:

```bash
set GEMINI_API_KEY=<key>
set AETHER_GEMINI_MODEL=gemini-3.5-flash
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/gemini_provider.py
```

The wrapper uses Gemini `generateContent` with JSON response formatting and still relies on local Aether validation before a patch is accepted.

Gemini transient `429`/`5xx` responses are retried inside the provider wrapper using parsed retry delays. Daily free-tier quota exhaustion is treated as non-retryable and should be reported as provider infrastructure failure, not as model quality, Aether safety, or false-acceptance evidence.

For the included OpenRouter provider wrapper:

```bash
set OPENROUTER_API_KEY=<key>
set AETHER_OPENROUTER_MODEL=<model-or-openrouter/free>
python benchmarks/run.py --suite agent --mode both --agent-adapter command --agent-command python benchmarks/agents/openrouter_provider.py
```

The wrapper uses OpenRouter's OpenAI-compatible chat-completions endpoint. For reproducible evidence, prefer a pinned `:free` model slug. Use `openrouter/free` only for smoke checks where free-model routing variance is acceptable. OpenRouter transient `429`/`5xx` responses are retried with parsed retry delays; daily quota or insufficient-credit failures should be reported as provider infrastructure failures.

## External Repository Runs

External git repository benchmarks require an explicit network flag:

```bash
python benchmarks/run.py --suite external-repository --mode all-modes --trials 3 --allow-network-repos
```

Manifests must pin immutable commit SHAs. Branch names are not acceptable evidence pins.

The deterministic external control condition emits the complete transformed source file. State and Aether emit structured patch JSON. Both conditions receive the same source and task description, but each input includes its actual output contract. Offline token comparisons use the recorded tokenizer and must remain separate from live provider telemetry.

Repository setup is measured independently and excluded from raw edit execution. `edit_to_verified_time_ms` includes transformation, syntax checking, and the task's declared verification command. Deterministic runs do not claim model generation latency.

## Statistical Reporting

For each important metric report:

- N
- mean
- median
- standard deviation
- minimum
- maximum

For control vs Aether comparisons, report:

- absolute difference
- relative difference
- success-rate difference
- failure-detection-rate difference

Use confidence intervals or statistical tests only when the sample size and task distribution support them.

## Evidence Language

Prefer:

```text
In this tested configuration, no syntax errors were observed in N cases.
```

Avoid:

```text
Aether guarantees syntactic correctness.
```

Each evidence report should separate observed result, inference, hypothesis, limitation, and future work.
