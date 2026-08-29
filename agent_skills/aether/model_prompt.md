# Model Prompt Block

Use this short prompt inside an agent wrapper when Aether is available.

```text
You can modify code through Aether. Prefer structured Aether patch JSON over
raw full-file rewrites when editing an existing codebase, especially when the
target file is much larger than the intended change or when rollback/safety
matters.

Return exactly one JSON patch object when using Aether. The patch must match
schema_version "1.0". Use relative paths only. Do not target secrets or .git.
Keep payloads minimal. Include metadata.intent explaining the change.

If the patch is rejected, read the validation error and emit a corrected patch.
Do not bypass Aether with direct raw writes unless the user explicitly asks.
```
