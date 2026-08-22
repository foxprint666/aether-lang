# Compiler Transition Planner

This note extends Aether beyond "patch JSON" into a compiler-style method planner for cheaper and faster AI programming. The goal is not to force one edit format everywhere. The goal is to choose the cheapest correct transition for the current job.

## Research Inputs

- Graphify maps code, docs, SQL schemas, configs, and media into a queryable graph. Its README says code is parsed locally with tree-sitter, graph edges are tagged as extracted or inferred, and it avoids vector-store-only retrieval. It also reports code-intelligence work on ERPNext where a graph tool improved key-fact coverage and avoided packing whole repos into context.
- Salsa models compiler work as memoized, demand-driven queries. Inputs change, revisions advance, and dependent functions are reused when their inputs are unchanged.
- Rowan/rust-analyzer use lossless green/red syntax trees: immutable green nodes store syntax, while red nodes provide parent/offset views. This makes source structure cheap to share between versions.
- Egg/e-graphs support equality saturation: many equivalent rewrites can coexist, then an extractor chooses the best form for a cost function.

These tools point at a larger Aether architecture:

```text
graph-scoped context -> transition planner -> wire format -> persistent syntax tree
                     -> incremental validation -> candidate rewrite search -> verification
```

## Why Small Programs Expose JSON Overhead

Small files are the weak case for structured patch JSON. If the whole target file is only a few hundred tokens, the schema envelope can be larger than the rewrite. Our current benchmarks already show that tiny JavaScript files can have negative patch-token savings.

That does not invalidate the mechanism. It means the planner must route small safe work differently:

- Tiny first draft or tiny safe edit: full-file generation can win.
- Focused edit in medium/large file: state transition usually wins on output tokens.
- Risky/security/failure-sensitive edit: guarded Aether wins on recovery and auditability.
- Repo-understanding task: graph-scoped context should reduce input tokens before any edit method is chosen.
- Optimization or equivalent rewrite task: e-graph search can explore candidates without forcing the agent to guess one perfect rewrite.

## Dynamic Method Set

The planner should choose among these methods:

| Method | Best Use | Main Cost | Safety |
| --- | --- | --- | --- |
| `full_file` | Tiny files, greenfield prototypes, agent patch-format uncertainty | Output grows with file size | Depends on verifier |
| `state_transition` | Focused edits where patch output is smaller than file rewrite | Schema envelope + AST apply | Verification only |
| `guarded_aether` | Sensitive changes, rollback cases, invalid patch defense | Validation + snapshot + rollback overhead | Strongest current path |
| `graph_scoped_state_transition` | Large repos where context retrieval dominates | Graph build/update + small query result | Same as state |
| `graph_scoped_guarded_aether` | Large-repo safety-sensitive edits | Graph query + Aether overhead | Strongest with reduced input |
| `synthesis_bundle` | Refactors/optimizations with multiple valid forms | E-graph saturation budget | Needs proof rules |

## Cost Model

The first practical planner can use a simple objective:

```text
score =
  input_tokens
  + output_tokens
  + local_latency_ms * latency_weight
  + failure_risk_penalty
  + safety_required_penalty_if_missing
```

Measured fields already present in benchmark records:

- `estimated_input_tokens`
- `estimated_output_tokens`
- `estimated_traditional_output_tokens`
- `edit_to_verified_time_ms`
- `task_success`
- `hybrid_selected_mode`

The new `benchmarks/analysis/transition_planner.py` uses those fields to estimate dynamic method selection and can apply a configurable graph-context savings factor.

## Graphify-Style Input Savings

Graph retrieval affects input tokens, not output patch size. In a large codebase, the agent should not read every relevant-looking file. It should ask the graph for the smallest connected subgraph that explains the task, then choose an edit method.

Expected effect:

```text
without graph = broad search + many raw file reads + edit output
with graph    = graph query result + selected source slices + edit output
```

This can compound with Aether:

```text
total savings ~= input-context savings + output-transition savings
```

But these savings are not additive in a naive way. A run with 80% input savings and 80% output savings does not automatically mean 160% total savings. The correct formula is:

```text
total_savings = 1 - ((new_input + new_output) / (old_input + old_output))
```

## Compiler-Theoretic Roadmap

Short term:

- Keep the existing threshold hybrid mode for production-like routing.
- Add benchmark-only transition planner analysis across old runs.
- Add graph-context fields to future benchmark descriptors: graph query tokens, raw file read tokens avoided, graph build/update time, and graph hit confidence.
- Improve Aether patch generation reliability with examples, stricter schemas, repair loops, and smaller wire forms.

Medium term:

- Add compact wire forms beyond JSON for small programs: CST paths, S-expressions, or a binary/CBOR-like internal transport when the caller is trusted.
- Add persistent syntax-tree snapshots for memory-backed rollback in local sessions, while retaining disk snapshots for durable safety.
- Add incremental invalidation metrics: affected functions, reused parse nodes, reused semantic queries, and recomputed query count.

Long term:

- Use Salsa-like query invalidation for semantic checks.
- Use Rowan-like green/red trees for O(depth) structural edits and cheap snapshots.
- Use e-graphs for bounded rewrite bundles where the agent submits alternatives and Aether extracts the cheapest verified form.

## Current Evidence Interpretation

The current result is nuanced:

- Aether/state transitions already show strong output-token savings on medium/larger focused edits.
- The first paired blind benchmark showed full-file generation was more reliable than Aether patch generation on a small 7-task set.
- That means the next breakthrough is not only "apply patches better." It is "help the agent choose and express the right transition with less ambiguity."

The planner approach is the bridge. It lets Aether be a hybrid programming substrate: full rewrite when that is cheaper, state transition when focused structure wins, guarded Aether when safety matters, and graph-scoped context when input tokens dominate.
