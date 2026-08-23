# Proposal: optional safe mutation backend for AUTO_FIX

Hi! First, thank you for making Healing Agent intentionally small and transparent. I like the thesis in the README: intelligence lives in the model and trust lives in the tests.

I would like to propose a small optional extension point for the file mutation step.

## Motivation

Healing Agent currently has a clear repair loop:

```text
exception -> generated fixed function -> backup -> replace function -> reload
```

That is a good minimal baseline. The risk is that when an AI-generated repair is wrong, the mutation may still touch the source file before the repaired module fails to reload or fails behavior checks.

For self-healing systems, this mutation step is where a safety backend can help:

```text
exception -> generated repair -> validate/sandbox/apply/verify -> commit or rollback
```

## Proposal

Add an optional mutation backend configuration:

```python
MUTATION_BACKEND = "direct"   # default, current behavior
MUTATION_BACKEND = "command"  # delegate mutation to an external command
MUTATION_COMMAND = "python path/to/safe_mutation_adapter.py"
```

The command backend would receive JSON on stdin:

```json
{
  "protocol_version": "healing-agent-mutation-v1",
  "source_file": "path/to/module.py",
  "function_name": "broken_function",
  "fixed_code": "def broken_function(...): ...",
  "error": {},
  "function_info": {}
}
```

It would return:

```json
{"ok": true}
```

or:

```json
{"ok": false, "rolled_back": true, "error": "hidden test failed"}
```

## Why command-based?

This keeps Healing Agent dependency-light and license-simple. It does not need to import any particular safe mutation system. Aether, a custom sandbox runner, or another verifier can sit outside the package.

## Related benchmark evidence

In a deterministic local self-healing A/B benchmark from Aether:

- valid repair success stayed equal: raw `1.0`, Aether-style `1.0`
- safety on invalid repairs improved: raw `0.0`, Aether-style `1.0`
- corruptions after failed attempts dropped from `3` to `0`
- output-token savings were `80.35%`
- output-byte savings were `85.44%`

This is not a production proof, but it suggests the mutation step is worth making pluggable.

Would you be open to a small PR that:

- preserves the existing direct replacer as default
- adds a generic command mutation backend
- adds tests for direct and command backends
- adds docs for Aether-style safe mutation integration?
