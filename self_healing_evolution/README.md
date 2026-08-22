# Aether for Self-Healing and Evolving Systems

This folder describes how Aether can contribute to autonomous self-healing loops, live repair systems, and continuously evolving software agents.

The core claim is simple:

> Self-healing systems become safer and cheaper when LLMs stop rewriting raw text and instead emit validated state transitions.

Traditional AI-driven autonomous systems often break down because the repair loop is text-native. The agent observes a bug, asks an LLM for a fix, receives a raw file rewrite or diff, and applies it directly or through a fragile patch command. That can introduce syntax errors, break imports, overwrite unrelated code, or leave the repository in a corrupted state after a failed attempt.

Aether changes the repair loop from raw text mutation into a deterministic control loop:

```text
Observe failure
  -> orient with code/runtime context
  -> ask model for structured patch intent
  -> validate schema/security/AST target
  -> snapshot current state
  -> apply in sandbox
  -> verify behavior
  -> commit or rollback
```

## Why This Matters

Self-healing systems need more than generation. They need controlled mutation.

| Problem in Raw LLM Repair | Aether Contribution |
|---|---|
| Full-file rewrites are expensive. | Small JSON operations target only the intended state transition. |
| Diffs can fail to apply. | Structured patch schema validates before mutation. |
| Generated code can be syntactically invalid. | AST-aware validation and language adapters catch failures earlier. |
| Failed fixes can corrupt a live codebase. | Snapshot-backed rollback restores the prior state when validation fails. |
| Autonomous loops need repeated attempts. | Lower output tokens make micro-repair and continuous evolution more economical. |
| Agents need measurable safety. | Benchmark records track success, rollback, corruption, tokens, latency, and cost. |

## Safe OODA Loop

Aether maps naturally onto an autonomous OODA loop:

1. **Observe:** Monitor detects an exception, test failure, latency regression, missing import, schema mismatch, or performance bottleneck.
2. **Orient:** System collects scoped code context, stack traces, failing tests, runtime metadata, and repository state.
3. **Decide:** LLM proposes an Aether patch or state transition instead of a full source rewrite.
4. **Act:** Aether validates, snapshots, sandboxes, verifies, then commits or rolls back.

This creates a self-healing loop where the AI agent proposes intent, but deterministic infrastructure decides whether that intent is allowed to touch the system.

## Risk-Free Live Mutation Target

The goal is not to trust every AI-generated patch. The goal is to make failed patches non-destructive.

Aether’s self-healing role is to place every autonomous repair behind:

- schema validation
- target-file validation
- sensitive-path rejection
- AST/syntax checks
- sandbox execution
- hidden or regression tests
- snapshot-backed rollback

In production terms, this aims for **zero codebase corruption on rejected repairs** and **no downtime from failed mutation attempts**. The benchmark program should continue measuring whether rollback succeeds across languages, failure types, and repository sizes.

## Continuous Evolution

Self-healing fixes failures. Evolution improves systems before they fail.

Aether can support controlled evolution loops such as:

- micro-refactoring
- dead-code removal
- import cleanup
- function extraction
- targeted performance optimization
- API migration
- test generation
- security hardening
- dependency compatibility fixes

Because an Aether patch can be far smaller than a full file rewrite, autonomous systems can afford to try more small improvements, reject unsafe ones, and keep only verified changes.

## Candidate GitHub Integration

Initial research found several public projects in this space:

| Candidate | Why It Matters |
|---|---|
| `matebenyovszky/healing-agent` | Python self-healing decorator that catches exceptions, generates fixes, tests them, backs up files, and optionally auto-fixes. Strong first candidate because Aether can replace raw code mutation with validated patch mutation. |
| `sola-st/RepairAgent` | Larger autonomous program-repair agent for Defects4J. Good later-stage benchmark, but heavier Java/Defects4J setup. |
| `distil-labs/distil-self-healing-agent` | End-to-end self-healing demo with control plane, telemetry, diagnosis, and remediation handoff. Good architecture comparison target. |
| `jalpatel11/Self-Healing-SRE-Agent` | SRE incident-response style self-healing workflow with multi-agent repair and validation. Good for workflow-level A/B testing. |

Recommended first target: **`matebenyovszky/healing-agent`**.

Reason: it is small enough to adapt quickly and directly exposes the critical point where Aether should help: replacing direct AI-generated source edits with validated state transitions and rollback-backed apply.

## What We Need To Prove

The self-healing/evolution claim should be measured, not assumed.

Minimum metrics:

- repair success rate
- syntax-valid repair rate
- test-pass repair rate
- rollback success rate
- repository corruption rate after failed fixes
- output token savings
- total token savings
- latency overhead
- cost per successful repair
- attempts per successful repair
- human-review issues

Success threshold for a serious claim:

> Aether-assisted healing must match or beat raw repair success while reducing output tokens and reducing or eliminating corruption after failed repair attempts.

## First Executable Evidence

The first deterministic local self-healing A/B benchmark is now implemented:

```text
benchmarks/run_self_healing_ab.py
```

Public report:

```text
benchmarks/results/public/SELF_HEALING_AB_REPORT.md
```

Current result:

- Raw repair success rate: `1.0`
- Aether repair success rate: `1.0`
- Raw safety success rate on invalid repair: `0.0`
- Aether safety success rate on invalid repair: `1.0`
- Raw corruptions after failed attempts: `1`
- Aether corruptions after failed attempts: `0`
- Aether output-token savings: `57.921811%`
- Self-healing gate passed: `true`

## Folder Map

- `AETHER_HEALING_LOOP.md` - detailed architecture for Aether-backed self-healing loops.
- `EXPERIMENT_PLAN.md` - concrete plan for adapting a public GitHub self-healing system.
- `benchmark_manifest_template.json` - starter manifest for reproducible self-healing A/B experiments.
- `candidate_repositories.json` - candidate public projects and selection notes.

## Candidate Sources

- `matebenyovszky/healing-agent`: https://github.com/matebenyovszky/healing-agent
- `sola-st/RepairAgent`: https://github.com/sola-st/RepairAgent
- `distil-labs/distil-self-healing-agent`: https://github.com/distil-labs/distil-self-healing-agent
- `jalpatel11/Self-Healing-SRE-Agent`: https://github.com/jalpatel11/Self-Healing-SRE-Agent
