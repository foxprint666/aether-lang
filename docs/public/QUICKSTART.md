# Aether Quickstart

Aether is a safety layer for AI coding agents. The agent emits a structured JSON
patch, Aether validates it, snapshots the project, applies the AST transition,
and keeps rollback available if the result is bad.

## Python Runtime

Install:

```bash
pip install aether-lang-runtime
```

Validate and apply:

```bash
aether validate patch.json
aether apply patch.json
aether snapshots
aether rollback <snapshot-id>
```

Machine-readable mode for agents:

```bash
aether validate patch.json --json
aether apply patch.json --json
```

Cloud-synced folders can hold locks open briefly. In OneDrive/Dropbox-style
workspaces, point Aether runtime state at a local cache directory:

```bash
aether --runtime-dir ~/.cache/aether/my-project apply patch.json
aether --runtime-dir ~/.cache/aether/my-project snapshots
aether --runtime-dir ~/.cache/aether/my-project rollback <snapshot-id>
```

Install the bundled skill for Codex:

```bash
aether skill install-codex
```

Other useful skill commands:

```bash
aether skill show
aether skill path
aether skill export ./aether-skill
```

`install-codex` copies `SKILL.md` into `CODEX_HOME/skills/aether` when
`CODEX_HOME` is set, otherwise into `~/.codex/skills/aether`.

## Node.js Runtime

The Node.js SDK now includes CLI parity for JavaScript/TypeScript patching.

From `sdk/node`:

```bash
npm install
npm run build
node dist/cli.js --project ../../examples/aether_js_cli validate ../../examples/aether_js_cli/patch.json
node dist/cli.js --project ../../examples/aether_js_cli apply ../../examples/aether_js_cli/patch.json
```

When packaged, the Node CLI exposes:

```bash
aether-js validate patch.json
aether-js apply patch.json
aether-js snapshots
aether-js rollback <snapshot-id>
```

## When Agents Should Use Aether

Use Aether when the agent can describe the change as a state transition:

- replace one function body
- add one function
- remove one function
- update imports
- replace a contextual block
- run a constrained script only with elevated trust

Prefer `modify_function`, `modify_class`, `add_function`, `remove_function`, and
`update_import` when a clear symbol exists. Use `replace_block` only as a
fallback for edits that do not map cleanly to a symbol, because context anchors
are easier to make ambiguous.
When `replace_block` is necessary, include both `context_before` and
`context_after`, and make `context_before` unique in the target file.

Use ordinary text editing for tiny one-off files where schema overhead is larger
than the change itself.

## Agent Loop

```text
Observe repo -> choose transition -> emit patch JSON -> validate -> apply -> test -> rollback or continue
```

The useful savings come from avoiding repeated full-file regeneration during
iterative repair, refactoring, and self-healing loops.
