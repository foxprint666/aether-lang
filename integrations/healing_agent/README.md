# Healing Agent VERIFY integration

This adapter lets [Healing Agent](https://github.com/matebenyovszky/healing-agent)
use Aether as a check-only verification gate in its `propose -> verify -> apply`
pipeline.

Configure Healing Agent with:

```python
VERIFY_COMMAND = ["python", "path/to/aether_verify_gate.py"]
```

Healing Agent runs the command in an isolated workspace where the candidate fix
has already been applied. The live source file is untouched while Aether checks
the candidate.

Contract:

- exit code `0` accepts the candidate;
- any nonzero exit code rejects it;
- `HEALING_AGENT_CANDIDATE` contains the candidate context JSON;
- stdout contains a small JSON verdict for logs.

Optional deeper checks can be chained through:

```text
AETHER_VERIFY_COMMAND="pytest tests/test_loader.py"
```
