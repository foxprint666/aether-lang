# Aether Local RAG Notes

## State Transitions

Aether represents code work as validated state transitions. A transition names a target symbol, an operation, and a payload. The goal is to avoid rewriting whole files when a smaller verified edit can express the change.

## Safety Model

Aether validates patches before applying them. The Python path uses snapshot-backed rollback. The JavaScript path uses the Node snapshot store and validation gates. A failed patch should not corrupt the repository.

## Agent Efficiency

The graph-scoped agent benchmark compared raw-source prompts against graph-scoped Aether prompts. The best graph-scoped contract reached 21 successful tasks out of 21. The raw-source patch baseline reached 12 successful tasks out of 21.

## Token Results

The graph-scoped contract used 7,878 context input tokens. The raw-source baseline used 32,274 context input tokens. The graph-scoped path saved 75.590258 percent of context input tokens and 60.298742 percent of total estimated tokens.

