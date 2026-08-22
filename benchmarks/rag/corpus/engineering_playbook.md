# Engineering Playbook

## Hybrid Policy

Aether should choose between raw generation and state transitions dynamically. Small greenfield files may not benefit from transition overhead. Larger files, patch tasks, and symbol-local changes benefit more because the agent can read and write less.

## RAG Goal

For retrieval augmented generation, the ultimate goal is to reduce both what the agent reads and what it writes while preserving answer quality. A raw RAG path retrieves large chunks. An Aether-style path retrieves graph-scoped facts and composes a compact answer state.

## Quality Rules

A local RAG benchmark should measure answer correctness, citation coverage, input tokens, output tokens, latency, and whether the answer uses unsupported claims. Quality must stay high while token use falls.

## Limitations

Prompt-level isolation is not the same as an operating system sandbox. Offline token counts are estimates and are not provider billing telemetry.

