# Aether CLI Example

This example shows the command surface intended for coding agents.

Install the Python runtime:

```bash
pip install aether-runtime
```

Validate a patch before touching files:

```bash
aether validate patch.json
```

Apply with validation, snapshot, and rollback-on-failure:

```bash
aether apply patch.json
```

Rollback manually when needed:

```bash
aether rollback <snapshot-id>
```

The import name remains `ai_runtime` for Python code:

```python
from ai_runtime import PatchOrchestrator
```

The `ae-safe` command is still available as a compatibility alias.
