# Context A/B Evidence Report

- Records: `7` across `4` repositories.
- Raw context tokens: `9589`.
- Graph-scoped context tokens: `3908`.
- Context-token savings: `59.244968%`.
- Context-byte savings: `63.932042%`.
- Target-symbol hit rate: `1.0`.
- Mean graph build time: `1.013114 ms`.

## Limitations

- This measures context packets and target selection, not a live agent's correctness from graph-scoped context.
- The graph extractor is lightweight and local; it is not yet a full Graphify integration.
- The task set is seven focused single-file tasks and does not cover whole-feature construction.
