# Self-Healing Loop A/B Report

- Tasks: `3`; trials: `1`.
- Baseline original failures: `2`.
- Raw repair success rate: `1.0`.
- Aether repair success rate: `1.0`.
- Raw safety success rate: `0.0`.
- Aether safety success rate: `1.0`.
- Raw corruptions after failed attempts: `1`.
- Aether corruptions after failed attempts: `0`.
- Aether corruption reduction: `1`.
- Aether output-token savings: `57.921811%`.
- Aether output-byte savings: `70.007517%`.
- Self-healing gate passed: `True`.
- Raw mean total task time: `174.857433 ms`.
- Aether mean total task time: `667.537267 ms`.

## Interpretation

- Valid repair tasks measure whether the healing loop can restore expected behavior.
- The invalid repair task measures whether failed autonomous mutation leaves the repository corrupted.
- Aether's advantage should be judged by repair quality plus corruption prevention plus token efficiency, not token savings alone.

## Limitations

- This is a deterministic local self-healing benchmark, not a live LLM study.
- The benchmark models a self-healing loop around a JavaScript service so it can run without Python SDK dependency friction.
- Raw healing intentionally represents direct full-file mutation; Aether healing represents structured patch mutation through validation and snapshot rollback.
