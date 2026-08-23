# Add optional command-based safe mutation backend

## Summary

This PR adds an optional mutation backend hook for the file-replacement step used by `AUTO_FIX`.

Default behavior is unchanged:

```python
MUTATION_BACKEND = "direct"
```

New optional behavior:

```python
MUTATION_BACKEND = "command"
MUTATION_COMMAND = "python path/to/safe_mutation_adapter.py"
MUTATION_TIMEOUT_SECONDS = 120
```

The command backend lets external safe-mutation systems validate, sandbox, apply, verify, and roll back a generated repair without becoming Healing Agent package dependencies.

## Why

Healing Agent already backs up sources and validates generated functions. This PR adds a small extension point for users who want an extra safety layer around mutation itself.

Example use cases:

- Aether-style structured patch validation
- sandboxed mutation
- hidden test verification before accepting a repair
- snapshot rollback if a repair fails after apply
- external mutation evidence logs

## Design

The backend receives JSON on stdin:

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

It returns:

```json
{"ok": true}
```

or:

```json
{"ok": false, "rolled_back": true, "error": "hidden test failed"}
```

## Files changed

- `healing_agent/mutation_backend.py`
- `healing_agent/healing_agent.py`
- `healing_agent/config_template.py`
- `tests/test_mutation_backend.py`
- `docs/aether-mutation-backend.md`

## Compatibility

- Default behavior remains `direct`.
- No Aether dependency is added.
- No provider dependency is added.
- Command backend is opt-in only.

## Validation

Syntax checks passed locally:

```bash
python -m py_compile healing_agent/mutation_backend.py healing_agent/healing_agent.py healing_agent/config_template.py tests/test_mutation_backend.py
```

Full pytest validation should be run in the upstream development environment:

```bash
python -m pytest
```
