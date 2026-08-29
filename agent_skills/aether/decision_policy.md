# Aether Agent Decision Policy

This policy helps an agent choose the cheapest safe coding method.

## Modes

| Mode | Use When | Tradeoff |
|---|---|---|
| Raw source | Tiny new snippets, docs, throwaway prototypes | Fast start, more generated tokens for larger code |
| State transition | Focused edits where a structured patch is enough | Lowest generated output, reduced safety envelope |
| Full Aether | Safety-sensitive edits, self-healing, rollback-needed work | More checks, strongest safety |
| Hybrid | General default for real agents | Picks cheap path for small tasks and guarded path for risky tasks |

## Simple Threshold

Use Aether when:

```text
estimated_patch_tokens <= estimated_full_rewrite_tokens * 0.8
```

Use full Aether instead of state-only when any of these are true:

- generated code will be applied automatically;
- failure could corrupt user data or source state;
- the patch touches imports, execution, filesystem, subprocess, network, or
  security-sensitive behavior;
- the task is part of a self-healing loop.

## Non-Programmer Version

Raw generation asks the model to write every brick.

Aether asks the model for the blueprint change, then lets infrastructure build,
check, and roll back the result.

## Current Evidence Anchors

- Risk project from-scratch benchmark: `87.751581%` generated-token savings.
- Risk project byte savings: `91.404559%`.
- Same quality in that run: accuracy `0.7361`, recall `1.0`.
- Self-healing mutation benchmark: invalid-change safety improved from `0.0`
  raw safety to `1.0` Aether safety, with corruptions reduced from `3` to `0`.

Treat these as local benchmark evidence, not a universal guarantee across all
models and repositories.
