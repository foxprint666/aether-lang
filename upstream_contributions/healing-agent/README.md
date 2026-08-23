# Healing Agent Upstream Contribution Package

Target repository:

```text
https://github.com/matebenyovszky/healing-agent
```

This folder contains a ready upstream contribution proposal for adding an optional safe mutation backend to Healing Agent.

## Why this contribution fits

Healing Agent already has a self-healing loop:

```text
exception -> AI-generated fixed function -> backup -> replace function -> reload
```

Aether's benchmark evidence suggests a useful next layer:

```text
exception -> AI-generated repair -> safe mutation backend -> validate/sandbox/rollback -> reload
```

The patch in this folder does **not** import Aether or add Aether as a dependency. It adds a generic command backend so safe mutation systems such as Aether can be plugged in externally while preserving Healing Agent's default behavior.

## Files

- `optional-safe-mutation-backend.patch` - complete upstream patch.
- `ISSUE_PROPOSAL.md` - issue text to open first.
- `PR_DESCRIPTION.md` - pull request description if the maintainer is receptive.

## Local validation performed

The local environment did not include `pytest`, so the full upstream test suite could not be run here.

Passed:

```text
python -m py_compile healing_agent/mutation_backend.py healing_agent/healing_agent.py healing_agent/config_template.py tests/test_mutation_backend.py
```

## Suggested upstream flow

1. Open the issue using `ISSUE_PROPOSAL.md`.
2. Wait for maintainer preference.
3. If accepted, fork `matebenyovszky/healing-agent`.
4. Apply `optional-safe-mutation-backend.patch`.
5. Run `python -m pytest`.
6. Open the PR using `PR_DESCRIPTION.md`.
