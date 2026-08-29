# Aether JS CLI Example

This example shows the Node.js runtime applying a structured JavaScript patch
with the `aether-js` command.

From `sdk/node`:

```bash
npm install
npm run build
node dist/cli.js --project ../../examples/aether_js_cli validate ../../examples/aether_js_cli/patch.json
node dist/cli.js --project ../../examples/aether_js_cli apply ../../examples/aether_js_cli/patch.json
node dist/cli.js --project ../../examples/aether_js_cli snapshots
```

The patch modifies `cart.js` by replacing only the `total` function body.

