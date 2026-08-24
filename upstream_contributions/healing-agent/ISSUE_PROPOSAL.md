# Proposal: optional command VERIFY gate

Hi! First, thank you for making Healing Agent intentionally small and transparent. I like the thesis in the README: intelligence lives in the model and trust lives in the tests.

I would like to propose a small optional extension point for the `VERIFY` stage in the `propose -> verify -> apply` design.

## Motivation

Healing Agent currently has a clear repair loop:

```text
exception -> generated fixed function -> backup -> replace function -> reload
```

That is a good minimal baseline. The next safety step is to reject bad candidates before the live source file is touched.

For self-healing systems, a command verifier gives external engines a narrow and testable role:

```text
exception -> generated repair -> isolated candidate -> command verify gate -> apply/reload
```

## Proposal

Add an optional command verification configuration:

```python
VERIFY_COMMAND = None  # default, current behavior
VERIFY_COMMAND = "python path/to/aether_verify_gate.py"
VERIFY_TIMEOUT_SECONDS = 120
```

The command would run in an isolated workspace where the candidate has already been applied. It would receive context through `HEALING_AGENT_CANDIDATE`:

```json
{
  "protocol": "healing-agent-candidate-v1",
  "source_file": "path/to/temp/module.py",
  "original_file": "path/to/live/module.py",
  "context": {}
}
```

It may return JSON detail:

```json
{"ok": true}
```

or:

```json
{"ok": false, "error": "hidden test failed"}
```

Exit code `0` passes; any nonzero exit rejects the candidate.

## Why command-based?

This keeps Healing Agent dependency-light and license-simple. It does not need to import any particular safe mutation system. Aether, pytest, a custom sandbox runner, or another verifier can sit outside the package.

## Related benchmark evidence

In a deterministic local self-healing A/B benchmark from Aether:

- valid repair success stayed equal: raw `1.0`, Aether-style `1.0`
- safety on invalid repairs improved: raw `0.0`, Aether-style `1.0`
- corruptions after failed attempts dropped from `3` to `0`
- output-token savings were `80.35%`
- output-byte savings were `85.44%`

This is not a production proof, but it suggests the verification step is worth making pluggable before apply.

Would you be open to a small PR that:

- preserves the current default behavior
- adds a generic command verify gate
- adds a real subprocess round-trip test
- adds docs for Aether-style verify-only integration?
