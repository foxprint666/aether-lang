# External Repository Efficiency Report

Experiments: `external-matrix-allmodes-trials3-v3`

## Evidence

- Records: `132/132` successful.
- Repositories: `5`; tasks: `12`.
- Languages: `javascript, python`.
- Verification levels: `{"behavior": 96, "safety": 12, "syntax": 24}`.
- Token estimators: `tiktoken:cl100k_base`.

## Matched Efficiency

| Comparison | Output-token savings | Total-token savings | Emitted-byte savings | Apply delta | Edit-to-verified delta |
|---|---:|---:|---:|---:|---:|
| State vs control | 67.40% | 24.59% | 74.67% | +82.49 ms | +80.80 ms |
| Aether vs control | 67.41% | 24.59% | 74.67% | +188.18 ms | +184.57 ms |
| Aether vs state | 0.04% | 0.01% | 0.00% | +105.70 ms | +103.77 ms |
| Hybrid vs control | 75.01% | 28.84% | 79.42% | +43.85 ms | +41.32 ms |

## Repositories

| Repository | Records | Success | Mean apply ms | Mean edit-to-verified ms |
|---|---:|---:|---:|---:|
| pallets-markupsafe | 30 | 100.00% | 39.49 | 115.45 |
| psf-requests | 24 | 100.00% | 60.59 | 125.47 |
| pypa-packaging | 24 | 100.00% | 45.51 | 123.76 |
| sindresorhus-escape-string-regexp | 30 | 100.00% | 140.64 | 218.37 |
| sindresorhus-yocto-queue | 24 | 100.00% | 160.25 | 234.50 |

## Hybrid

- Success rate: `100.00%`.
- Valid-task selected modes: `{"control": 12, "state": 18}`.
- Safety-task selected modes: `{"aether": 6}`.
- Mean estimated output savings: `-20.43%`.

## External Rollback

- Safety records: `12/12` successful.
- Failure detection: `100.00%`.
- Rollback success: `100.00%`.

## Source Size

- Positive output savings: `60.00%`.
- Pearson correlation, source bytes vs savings: `0.45`.

## Limitations

- Token values are offline tokenizer estimates, not provider billing telemetry.
- Generation latency and model retries are absent because these tasks use deterministic reference patches.
- Control execution measures writing an already-generated complete file; model generation time is not fabricated.
- Raw execution time excludes repository checkout and verification; edit_to_verified_time_ms includes syntax and declared task verification.
- Behavior-level verification is reported separately from syntax-only verification.
