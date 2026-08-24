# Healing Agent Upstream Contribution Package

Target repository:

```text
https://github.com/matebenyovszky/healing-agent
```

This folder contains the revised upstream contribution proposal for adding an optional `VERIFY_COMMAND` gate to Healing Agent.

## Why this contribution fits

Healing Agent already has a self-healing loop:

```text
exception -> AI-generated fixed function -> backup -> replace function -> reload
```

Aether's benchmark evidence suggests a useful verification layer:

```text
exception -> AI-generated repair -> isolated candidate -> Aether verify gate -> apply/reload
```

The patch in this folder does **not** import Aether or add Aether as a dependency. It adds a generic command verifier so safety systems such as Aether can be plugged in externally while preserving Healing Agent's default behavior.

## Files

- `verify-command-gate.patch` - complete upstream patch.
- `ISSUE_PROPOSAL.md` - issue text to open first.
- `PR_DESCRIPTION.md` - pull request description if the maintainer is receptive.

## Local validation performed

Passed:

```text
uv run --extra dev pytest tests/test_verify_gate.py --basetemp=<outside-repo> -p no:cacheprovider
uv run --extra dev pytest tests/test_verify_gate.py tests/test_restore_on_failure.py tests/test_git_patch_saver.py -m "not live" --basetemp=<outside-repo> -p no:cacheprovider
uv run --extra dev pytest -m "not live" --basetemp=<outside-repo> -p no:cacheprovider
```

Result: `106 passed, 1 skipped, 11 deselected`.

## Suggested upstream flow

1. Keep the existing PR open.
2. Replace the mutation-backend hook with `verify-command-gate.patch`.
3. Run the non-live test suite.
4. If a live provider is configured, run `tests/test_data_drift.py` with Aether as `VERIFY_COMMAND`.
5. Update the PR using `PR_DESCRIPTION.md`.
