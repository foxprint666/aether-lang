# Aether Agent Skill

Use this skill when an AI coding agent needs to modify code safely or cheaply.
Aether is not a model. It is a structured execution layer for code changes.

## Core Rule

Prefer an Aether patch when the task is an edit to an existing codebase and the
change can be expressed as a structured state transition.

Prefer raw source generation only when the user asks for a new tiny snippet,
an explanation, or a disposable prototype where rollback and audit do not
matter.

## Mental Model

Do not think "rewrite this file." Think:

```text
observe target -> select operation -> emit patch JSON -> validate -> snapshot -> apply -> verify -> rollback on failure
```

## Required Output In Aether Mode

Emit one JSON object matching Aether patch schema version `1.0`:

```json
{
  "schema_version": "1.0",
  "patch_id": "00000000-0000-4000-8000-000000000000",
  "action": "modify_function",
  "target": {
    "file": "src/app.py",
    "symbol": "calculate_total",
    "symbol_type": "function"
  },
  "changes": {
    "operation": "replace_body",
    "payload": "return sum(items)\n"
  },
  "metadata": {
    "agent_id": "agent-name",
    "model": "model-name",
    "intent": "Fix total calculation."
  }
}
```

Use a real UUID v4 for `patch_id`.

## Operation Selection

- Use `modify_function` + `replace_body` for focused function repairs.
- Use `modify_class` + `replace_body` for class-level replacement.
- Use `update_import` + `add_import` or `remove_import` for imports.
- Use `replace_block` + `context_replace` only when no symbol-level edit fits.
- Use `run_script` + `run` only for trusted scripts or explicit maintenance
  tasks, and keep `allow_network` false unless the user explicitly needs it.

## Safety Rules

- Never target absolute paths.
- Never target `.env`, `.git`, credentials, SSH keys, token files, or secrets.
- Keep payloads minimal.
- Include `constraints.timeout_ms` for executable changes.
- Let Aether reject invalid or unsafe patches. Do not bypass validation by
  falling back to raw writes unless the user explicitly asks.
- If Aether rejects the patch, report the structured error and produce a
  corrected patch.

## Efficiency Rules

Use Aether when:

- the target file is larger than the intended change;
- the same pattern is repeated across files;
- the agent would otherwise rewrite boilerplate;
- correctness, rollback, or audit matters;
- a self-healing loop is applying a generated change.

Use raw generation when:

- creating a tiny new one-off file;
- writing docs/prose;
- the codebase does not have an Aether runtime available;
- the requested change is too ambiguous to encode safely.

## Verification Habit

After applying a patch, run the smallest meaningful check:

```bash
python -m pytest <focused-test>
```

or:

```bash
npm test -- <focused-test>
```

For Python SDK callers, prefer:

```python
from ai_runtime import PatchOrchestrator

result = PatchOrchestrator(project_root=".").apply(patch)
if not result.ok:
    print(result.errors)
```

For CLI-based agent wrappers, prefer:

```bash
aether validate patch.json
aether apply patch.json
aether rollback <snapshot-id>
```

## Report Format

When reporting results, include:

- whether validation passed;
- whether snapshot was committed or rolled back;
- tests run;
- generated patch token/byte size if comparing efficiency;
- limitations or checks not run.
