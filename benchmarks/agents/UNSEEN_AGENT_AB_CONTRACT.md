# Unseen Agent A/B Command Contract

`benchmarks/run_unseen_agent_ab.py` runs matched coding-agent trials where hidden tests stay inside the evaluator.

Each command receives one JSON descriptor on stdin and must print one JSON object on stdout.

## Raw full-file arm

Input descriptor field:

- `arm`: `raw_full_file`

Required output:

```json
{
  "content": "complete updated source file",
  "usage": {
    "input_tokens": 123,
    "output_tokens": 456,
    "tool_calls": 1,
    "latency_ms": 900,
    "cost_usd": 0.001,
    "model": "provider/model"
  }
}
```

## Aether patch arm

Input descriptor field:

- `arm`: `aether_patch`

Required output:

```json
{
  "patch": {
    "schema_version": "1.0",
    "patch_id": "uuid",
    "action": "modify_function",
    "target": {
      "file": "app.js",
      "symbol": "total",
      "symbol_type": "function"
    },
    "changes": {
      "operation": "replace_body",
      "payload": "return add(20, 22);"
    },
    "metadata": {
      "agent_id": "agent-name",
      "model": "provider/model"
    }
  },
  "usage": {
    "input_tokens": 123,
    "output_tokens": 45,
    "tool_calls": 1,
    "latency_ms": 900,
    "cost_usd": 0.001,
    "model": "provider/model"
  }
}
```

`usage` is optional, but live/provider runs should include it so reports can separate estimated tokens from provider telemetry.

## Smoke command

```bash
python benchmarks/run_unseen_agent_ab.py \
  --experiment-id unseen-agent-smoke-v1 \
  --raw-command python benchmarks/agents/unseen_smoke_agent.py \
  --aether-command python benchmarks/agents/unseen_smoke_agent.py
```

Then publish evidence:

```bash
python benchmarks/analysis/unseen_agent_ab_evidence.py \
  benchmarks/results/raw/unseen-agent-smoke-v1.json \
  --json-output benchmarks/results/public/unseen_agent_ab_evidence.json \
  --markdown-output benchmarks/results/public/UNSEEN_AGENT_AB_REPORT.md
```
