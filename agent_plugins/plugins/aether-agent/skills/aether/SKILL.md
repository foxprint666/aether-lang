---
name: aether
description: Use Aether structured patches for safer and cheaper code edits with validation, snapshots, apply, and rollback.
---

# Aether Agent Skill

Use Aether when an AI coding agent needs to modify an existing codebase safely
or with fewer generated tokens. Aether is not a model; it is a structured
execution layer for code changes.

## Choose The Mode

- Use raw code for tiny throwaway snippets, prose, or one-file prototypes.
- Use Aether patch JSON for focused edits to existing files.
- Use full Aether apply when rollback, audit, self-healing, or safety matters.

## Emit This Shape

```json
{
  "schema_version": "1.0",
  "patch_id": "11111111-1111-4111-8111-111111111111",
  "action": "modify_function",
  "target": {
    "file": "src/cart.py",
    "symbol": "calculate_total",
    "symbol_type": "function"
  },
  "changes": {
    "operation": "replace_body",
    "payload": "return sum(item.price * item.quantity for item in items)\n"
  },
  "metadata": {
    "agent_id": "codex",
    "model": "current",
    "intent": "Calculate totals using price times quantity."
  }
}
```

Use a real UUID v4 for `patch_id`.

## Run Commands

```bash
aether validate patch.json
aether apply patch.json
aether rollback <snapshot-id>
```

Use `aether apply` for the full validate, snapshot, apply, and rollback-on-fail
path. Use `aether validate` when you only need a preflight check.

## Safety Rules

- Use relative paths only.
- Never target `.env`, `.git`, credentials, SSH keys, token files, or secrets.
- Keep payloads minimal.
- If Aether rejects a patch, read the structured error and emit a corrected
  patch instead of bypassing Aether.

## Efficiency Rule

Use Aether when the patch JSON is likely at least 20% smaller than a full-file
rewrite, or whenever safety matters. In local from-scratch module benchmarks,
Aether-style transitions preserved quality while saving about 87-88% generated
tokens.
