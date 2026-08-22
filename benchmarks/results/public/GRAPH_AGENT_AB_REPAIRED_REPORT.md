# Graph-Scoped Agent A/B Report

- Matched pairs: `21` across `7` tasks.
- Raw-source patches: `12/21` (`0.571429`).
- Graph-scoped patches: `18/21` (`0.857143`).
- Success difference: `28.5714` percentage points.
- Discordant pairs: raw-only `0`, graph-only `6`; exact McNemar p `0.03125`.
- Graph context-input token savings: `75.590258%`.
- Graph total-token savings: `60.675558%`.

## Limitations

- Graph-scoped generation used fresh stateless Codex subagents with prompt-enforced source-only restrictions, not OS-enforced filesystem denial.
- Raw-source comparison reuses the prior paired blind patch trials; graph-scoped trials are newly generated.
- Token counts are offline tiktoken estimates, not provider billing telemetry.
