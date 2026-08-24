# Add optional command-based VERIFY gate

## Summary

This PR adds an optional command verifier for the `VERIFY` stage described in `docs/apply-verify-design.md`.

Default behavior is unchanged:

```python
VERIFY_COMMAND = None
```

New optional behavior:

```python
VERIFY_COMMAND = "python path/to/aether_verify_gate.py"
VERIFY_TIMEOUT_SECONDS = 120
```

The command gate runs against an isolated candidate copy before Healing Agent changes the live source file. Exit code `0` accepts the candidate; any nonzero exit rejects it. Protocol-aware engines can read `HEALING_AGENT_CANDIDATE` and print JSON detail to stdout.

## Why

This matches the 0.4 `propose -> verify -> apply` direction from the maintainer discussion: Aether and similar tools can provide sandbox/hidden-test validation without taking over Healing Agent's write path.

Example use cases:

- Aether check mode
- hidden test verification before accepting a repair
- repository-specific pytest/ruff gates
- structured JSON failure detail for logs

## Design

The command receives candidate context via `HEALING_AGENT_CANDIDATE`:

```json
{
  "protocol": "healing-agent-candidate-v1",
  "source_file": "path/to/temp/module.py",
  "original_file": "path/to/live/module.py",
  "context": {}
}
```

It returns:

```json
{"ok": true}
```

or:

```json
{"ok": false, "error": "hidden test failed"}
```

The exit code, not the JSON body, decides pass/fail.

## Files changed

- `healing_agent/verify_gate.py`
- `healing_agent/healing_agent.py`
- `healing_agent/config_template.py`
- `healing_agent/config_loader.py`
- `tests/test_verify_gate.py`
- `docs/aether-verify-gate.md`

## Compatibility

- Default behavior remains unchanged when `VERIFY_COMMAND = None`.
- No Aether dependency is added.
- No provider dependency is added.
- Command verification is opt-in only.

## Validation

Passed locally:

```bash
uv run --extra dev pytest tests/test_verify_gate.py --basetemp=<outside-repo> -p no:cacheprovider
uv run --extra dev pytest tests/test_verify_gate.py tests/test_restore_on_failure.py tests/test_git_patch_saver.py -m "not live" --basetemp=<outside-repo> -p no:cacheprovider
uv run --extra dev pytest -m "not live" --basetemp=<outside-repo> -p no:cacheprovider
```

Full non-live result: `106 passed, 1 skipped, 11 deselected`.
