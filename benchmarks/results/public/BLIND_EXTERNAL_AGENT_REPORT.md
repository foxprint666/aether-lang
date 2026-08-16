# Blind External-Agent Report

- Records: `96` across `24` independent generation events.
- Successful generated patches: `16/24` (`0.666667`).
- Record success rate: `0.666667`.
- Coverage: `5` repositories, `8` tasks, `javascript, python`.
- Blind records: `96`; oracle-used records: `0`.
- Hash-matched patches across modes: `24/24`.
- Estimated output-token savings versus full-file output: `78.230854%`.
- Emitted-byte savings versus full-file output: `82.801988%`.

## Modes

- `aether`: `0.666667` success, `339.77735 ms` mean edit-to-verified.
- `control`: `0.666667` success, `228.4326 ms` mean edit-to-verified.
- `hybrid`: `0.666667` success, `194.8971 ms` mean edit-to-verified.
- `state`: `0.666667` success, `224.46655 ms` mean edit-to-verified.

Relative to direct control, Aether was `48.742933%`, state was `-1.736201%`, and hybrid was `-14.680698%` in mean edit-to-verified time (positive is slower; negative is faster).

## Limitations

- Generation used independent Codex subagents with prompt-level packet restrictions, not an OS-enforced filesystem sandbox.
- The stored provider replays hash-locked agent outputs; it does not claim live provider token, latency, retry, or cost telemetry.
- Control, state, Aether, and hybrid apply the same generated structured patch; this isolates application safety and overhead but is not a full-file-generation agent control arm.
- Tasks were unpublished before generation but are revealed with this evidence bundle, so future blind trials require fresh tasks.
