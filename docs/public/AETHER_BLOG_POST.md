# Aether: Moving AI Coding Agents From Token Rewrites to State Transitions

AI coding agents are getting stronger every month, but most of them still edit code in a surprisingly fragile way: they generate text, diffs, or whole-file rewrites, then hope the result lands correctly.

That works often enough to feel magical. It also fails often enough to be expensive.

Large language models are excellent at reasoning about code, but asking them to repeatedly rewrite source files is a costly interface. Every rewrite burns tokens. Every malformed edit can break syntax. Every bad patch forces retries, debugging, or manual recovery.

Aether explores a different idea:

> What if an AI coding agent changed code through structured state transitions instead of raw token generation?

Instead of saying, “rewrite this file,” the agent says:

```text
modify this function
add this import
replace this block
remove this function
```

The framework then applies that change through an AST/state-transition engine, optionally with validation, snapshots, rollback, and audit metadata.

## The Core Idea

Traditional AI coding is like asking someone to rewrite an entire page to fix one sentence.

Aether is like saying:

> Change this exact sentence, verify the page still works, and roll back if it breaks.

This gives us three operating modes:

- `control`: the baseline direct edit/full rewrite path.
- `state`: the fast state-transition path, without the full safety wrapper.
- `aether`: the guarded path with validation, snapshots, and rollback.
- `hybrid`: a practical router that picks the right path depending on task size and safety needs.

That last mode matters. Aether should not be forced everywhere. If a file is tiny, a rewrite can be cheaper. If the file is larger and the change is focused, a state transition can save tokens. If the edit is risky, full Aether safety is worth the overhead.

## What We Tested

We built a reproducible benchmark suite covering:

- deterministic correctness cases,
- Python and JavaScript AST patching,
- failure injection,
- rollback behavior,
- replay-agent generated patches,
- command/provider adapters,
- local real-repository workloads,
- five pinned external GitHub repositories,
- state-only fast path,
- hybrid threshold routing,
- token estimates,
- Phase 4/5/6/7 readiness gates.

The public benchmark bundle now reports:

- Phase 4 real repositories: `18/18`, `100%`
- Phase 5 cross-language recovery: `54/54`, `100%`
- Phase 6 A/B agent benchmark: `48/48`, `100%`
- Tested proof scope: `321/321`, `100%`
- External repository matrix: `132/132`, `100%`
- Phase 7 readiness: `true`

The external matrix uses immutable commits from MarkupSafe, Packaging, Requests, escape-string-regexp, and yocto-queue. It contains ten valid edit tasks and two rollback tasks across Python and JavaScript, repeated over three trials. Of the 132 records, 96 use behavior-level checks, 24 use syntax-level checks, and 12 exercise guarded rollback.

This matters because it moves the work beyond synthetic-only tests.

## Efficiency Results

The strongest live provider signal came from OpenRouter smoke evidence:

- Output-token savings: `83.26%`
- Total-token savings: `60.10%`

The local benchmark also showed where the threshold lives.

In the three-trial external matrix, hybrid versus full-file control measured:

- Output-token savings: `75.01%`
- Estimated total-token savings: `28.84%`
- Emitted-byte savings: `79.42%`
- Mean edit-to-verified delta: `+41.32 ms`
- Bootstrap 95% interval for that time delta: `+15.50 ms` to `+71.03 ms`

These are offline `tiktoken:cl100k_base` estimates, not provider billing telemetry. The control prompt asks for a complete updated file; the state/Aether prompt asks for structured patch JSON. No model generation latency was invented.

For very small files, structured patch JSON can be larger than rewriting the whole file. That is expected. Aether has fixed metadata overhead.

But as files grow, traditional rewrite cost grows with the file, while Aether patch cost mostly grows with the change.

Roughly:

```text
Traditional rewrite cost = size of whole file
Aether/state patch cost = size of change + fixed metadata
```

That means Aether becomes more attractive when:

- files are medium or large,
- changes are focused,
- agents make many edits,
- safety and rollback matter,
- multiple agents touch the same codebase,
- retries are expensive.

The hybrid mode makes this explicit. In our benchmark:

- Hybrid selected `control` for tiny/safe records.
- Hybrid selected `state` for records that cleared the token-savings threshold.
- Hybrid selected full `aether` for safety/failure cases.

On the ten valid external tasks over three trials, hybrid routed 12 tiny-file records to control and 18 larger-file records to state. The six safety records were routed to full Aether. Only `60%` of valid matched tasks had positive patch-token savings before routing: files below 1 KiB averaged negative savings, while the 1-4 KiB bucket averaged `57.31%` savings and the 4-16 KiB bucket averaged `94.25%`.

## Safety Results

Efficiency is only half the story.

The bigger institutional value may be reliability.

In the tested scope:

- Invalid patch detection: `100%`
- False acceptance: `0%`
- Rollback success in tested rollback cases: `100%`
- External Python/JavaScript rollback: `12/12`, `100%`
- Phase 7 public readiness: `true`

That does not mean Aether is universally safe. It means the tested benchmark scope passed, and the evidence is reproducible.

That distinction matters. AI infrastructure should not make magical claims. It should produce raw results, exact commands, and known limitations.

## Why This Could Matter

Most current coding agents still treat code modification as text generation.

Aether treats modification as an operation on code state.

That is a subtle but important shift.

Future coding agents will likely become more tool-native, more parallel, and more autonomous. In that world, raw file rewrites are a weak coordination mechanism. Agents need structured ways to say exactly what they intend to change, and systems need ways to validate, apply, audit, and roll back those changes.

Aether is an early step in that direction.

It is not trying to replace LLM reasoning. It is trying to give LLM reasoning a safer execution layer.

## Is This Novel?

AST tools exist. Patch systems exist. Sandboxes exist. Rollback systems exist.

The novel contribution is putting these pieces together as an agent-facing programming workflow:

```text
agent intent -> structured state transition -> validation -> apply -> verify -> rollback if needed
```

Then adding a benchmark program that asks a practical question:

> When should an agent rewrite code, when should it use a fast state transition, and when should it use full safety?

That is the important contribution.

Not “Aether is always faster.”

The better claim is:

> Aether gives AI coding agents a measured, hybrid execution model for choosing between speed, token efficiency, and safety.

That is useful enough to be worth serious attention.

## The Honest Limitations

The benchmark is promising, not final proof.

Current limitations:

- Live provider token/cost evidence is still small.
- External repository coverage is five repositories and twelve tasks, still not a representative sample of all projects.
- State mode is faster but does not include full validation/snapshot/rollback safety.
- Hybrid routing is a practical threshold policy, not a universally optimal policy.
- More real-world agent trials are needed.

But the evidence is now strong enough to justify the next step: broader public replication.

## Why Institutions Should Care

If an institution already spends heavily on AI coding agents, even partial savings matter.

Using the observed `60.10%` total-token savings as a theoretical scenario:

| Current AI coding spend | Potential token savings |
|---:|---:|
| `$10,000/month` | about `$6,010/month` |
| `$50,000/month` | about `$30,050/month` |
| `$100,000/month` | about `$60,100/month` |
| `$1,000,000/year` | about `$601,000/year` |

That is only token spend.

If safer patching also saves developer recovery time, the economics improve further. A 50-developer team saving just 10 minutes per developer per workday is about 183 hours/month. At `$80/hour`, that is roughly `$14,667/month`, or `$176,000/year`.

These are theoretical numbers, but they show why the direction matters.

## Closing

The future of AI coding will not only be better models.

It will also be better interfaces between models and codebases.

Aether is a bet on that future:

> Agents should not just generate code. They should perform safe, measurable state transitions.

That shift could make coding agents cheaper, safer, and more useful inside real engineering organizations.

The current benchmark evidence does not prove everything.

But it proves enough to move from idea to serious experiment.
