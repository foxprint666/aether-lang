# Paired Blind Agent-Generation Report

- Matched pairs: `21` across `7` tasks and `3` trials.
- Aether patches: `10/21` (`0.47619`).
- Full files: `17/21` (`0.809524`).
- Success difference: `-33.3334` percentage points; task-clustered bootstrap 95% interval `[-47.619048, -19.047619]`.
- Discordant pairs: Aether-only `0`, full-file-only `7`; exact McNemar p `0.015625`.
- Aether output-token savings: `82.350605%`; byte savings: `86.117637%`.
- Original revisions already passing hidden checks: `0`.

## Limitations

- Generation used fresh stateless Codex subagents with prompt-enforced source-only restrictions, not OS-enforced filesystem denial.
- Offline token counts are tiktoken estimates; provider generation latency, retries, and monetary cost were not available.
- The 7 tasks come from 4 pinned repositories and are not representative of all coding-agent workloads.
- These tasks became public with this evidence and must not be reused as unseen tasks.
