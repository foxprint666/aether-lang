# Aether Agent Build Demo

This example was built by applying structured Aether patches instead of directly writing
the final Python source file.

Run the patches:

```bash
for %f in (patches\*.json) do aether --project . apply "%f"
```

Run the tests:

```bash
python -m pytest tests -q
```

