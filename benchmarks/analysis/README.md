# Analysis

Future analysis scripts should compute:

- N
- mean
- median
- standard deviation
- minimum
- maximum
- absolute difference
- relative difference
- success-rate difference

Only analyze raw benchmark output. Do not manufacture statistical significance.

Implemented scripts:

- `summarize.py`: summarizes one raw benchmark result.
- `efficiency.py`: compares matched control/Aether pairs and provider telemetry.
- `state_efficiency.py`: compares matched `control`, `state`, and `aether` records across one or more raw results.
- `hybrid_policy.py`: summarizes `hybrid` mode routing decisions and threshold outcomes.
- `token_estimates.py`: summarizes offline token estimates for structured patch output versus full-file rewrite output.
- `phase_gates.py`: evaluates the current Phase 4/5/6 completion gates.
- `phase7_readiness.py`: evaluates whether the local evidence is packaged well enough for a reproducible public benchmark bundle.
- `proof_score.py` and `proof_gaps.py`: report conservative evidence maturity and remaining proof gaps.
