# Experiment Plan: Aether-Assisted Self-Healing

This plan describes how to test whether Aether improves an existing self-healing or autonomous repair system.

## Research Question

Can Aether make self-healing software loops safer, cheaper, and more repeatable by replacing raw text edits with validated state transitions?

## First Candidate

Target repository:

```text
https://github.com/matebenyovszky/healing-agent
```

Reason for choosing it first:

- It is already a self-healing Python agent.
- It detects exceptions and generates fixes.
- It supports optional auto-fix behavior.
- Its README describes backups before fixes, which gives us a clean baseline to compare against Aether snapshots and rollback.
- The project is smaller and easier to adapt than Defects4J-scale repair systems.

## Baseline vs Aether Conditions

### Baseline

Use the self-healing system as designed:

```text
exception -> context capture -> LLM fix -> raw source edit -> test -> backup restore if needed
```

### Aether Condition

Insert Aether between the LLM and disk mutation:

```text
exception -> context capture -> LLM Aether patch -> validation -> snapshot -> sandbox apply -> test -> commit/rollback
```

## Integration Adapter

The adapter should translate the healing agent's repair request into the Aether command contract:

```json
{
  "incident_id": "example-001",
  "language": "python",
  "source_file": "broken_service.py",
  "failure": {
    "type": "TypeError",
    "traceback": "...",
    "expected_behavior": "..."
  },
  "output_contract": "Return an Aether 1.0 patch only."
}
```

The LLM output should be:

```json
{
  "schema_version": "1.0",
  "patch_id": "uuid",
  "action": "modify_function",
  "target": {
    "file": "broken_service.py",
    "symbol": "target_function",
    "symbol_type": "function"
  },
  "changes": {
    "operation": "replace_body",
    "payload": "..."
  },
  "metadata": {
    "agent_id": "healing-agent-aether-adapter"
  }
}
```

## Task Set

Start with synthetic but realistic failures:

1. missing import
2. wrong function return type
3. key error from changed schema
4. None handling bug
5. off-by-one loop
6. invalid JSON parse handling
7. dependency compatibility shim
8. JavaScript syntax invalid rollback case
9. Python syntax invalid rollback case
10. performance micro-optimization

Then move to public repository tasks:

- small Python libraries
- small JavaScript libraries
- one self-healing framework repository
- one service-style repository with runtime probes

## Metrics

Primary:

- repair success rate
- hidden test pass rate
- rollback success rate
- repository corruption rate
- output-token savings
- cost per successful repair

Secondary:

- input-token usage
- latency
- attempts per repair
- syntax failure rate
- validation rejection rate
- patch size
- review issues

## Pass Criteria

Aether-assisted healing passes the first gate if:

- repair success rate is equal to or greater than baseline
- repository corruption on failed attempts is lower than baseline
- rollback success is 100% on rejected/failed Aether mutations in the tested cases
- output-token savings are at least 20%

The stronger claim requires:

- at least 20 unseen tasks
- at least 3 trials per task
- multiple models or agent commands
- at least 2 languages
- at least 2 real repositories

## Expected Outcome

The likely near-term outcome:

- Aether will reduce output tokens strongly on localized repairs.
- Aether will add some validation overhead.
- Aether will perform best when files are medium/large and changes are localized.
- Aether will be much safer on invalid or risky patches because failed mutations can be rejected or rolled back deterministically.

## Implemented First Local Runner

The first deterministic local runner now exists:

```text
benchmarks/run_self_healing_ab.py
```

The runner should:

1. create a failing project
2. run baseline self-healing repair
3. run Aether-assisted repair
4. inject invalid repair attempts
5. verify rollback
6. emit raw JSON, processed CSV, and public evidence

Current evidence:

- `benchmarks/results/raw/self-healing-ab-v1.json`
- `benchmarks/results/processed/self-healing-ab-v1.csv`
- `benchmarks/results/public/SELF_HEALING_AB_REPORT.md`
- `benchmarks/results/public/self_healing_ab_evidence.json`

Observed first-run result:

- Raw repair success rate: `1.0`
- Aether repair success rate: `1.0`
- Raw safety success rate on invalid repair: `0.0`
- Aether safety success rate on invalid repair: `1.0`
- Raw corruptions after failed attempts: `1`
- Aether corruptions after failed attempts: `0`
- Aether output-token savings: `57.921811%`
- Self-healing gate passed: `true`

Next step: extend the runner to clone and adapt `matebenyovszky/healing-agent`.
