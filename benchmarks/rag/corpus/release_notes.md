# Aether Benchmark Release Notes

## Graph Context

The graph context extractor identifies Python functions, Python classes, JavaScript functions, JavaScript classes, and JavaScript class methods. It now recognizes generator methods such as `* drain()`.

## JavaScript Engine

The Node AST engine can replace generator method bodies that contain `yield`. This matters for methods such as `Queue.drain`, where the replacement body must preserve generator behavior.

## Public Evidence

Public evidence is stored as raw JSON, processed CSV, and Markdown reports under `benchmarks/results`. The graph-scoped contract v2 report is `benchmarks/results/public/GRAPH_AGENT_AB_CONTRACT_V2_REPORT.md`.

