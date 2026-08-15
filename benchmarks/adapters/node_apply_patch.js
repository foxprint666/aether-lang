#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { performance } = require("perf_hooks");

const repoRoot = path.resolve(__dirname, "..", "..");
const nodeSdk = path.join(repoRoot, "sdk", "node");

const distIndex = path.join(nodeSdk, "dist", "index.js");
const distAstEngine = path.join(nodeSdk, "dist", "ast", "engine.js");
let sdk;
let astEngine;

if (fs.existsSync(distIndex) && fs.existsSync(distAstEngine)) {
  sdk = require(distIndex);
  astEngine = require(distAstEngine);
} else {
  process.env.TS_NODE_TRANSPILE_ONLY = "true";
  process.env.TS_NODE_COMPILER_OPTIONS = JSON.stringify({ module: "CommonJS" });
  require(path.join(nodeSdk, "node_modules", "ts-node", "register"));
  sdk = require(path.join(nodeSdk, "src", "index.ts"));
  astEngine = require(path.join(nodeSdk, "src", "ast", "engine.ts"));
}

const { applyPatch } = astEngine;
const { PatchEngine, SnapshotStore } = sdk;

async function main() {
  const [workdir, patchPath, mode = "--aether"] = process.argv.slice(2);
  if (!workdir || !patchPath) {
    throw new Error("Usage: node_apply_patch.js <workdir> <patch-json-path> [--aether|--unchecked]");
  }

  const patch = JSON.parse(fs.readFileSync(patchPath, "utf8"));
  if (mode === "--unchecked") {
    applyPatch(patch, workdir);
    process.stdout.write(JSON.stringify({
      ok: true,
      validation_failed: false,
      validation_time_ms: null,
      rolled_back: false,
      errors: [],
    }));
    return;
  }

  const validationStart = performance.now();
  const engine = new PatchEngine(undefined, workdir);
  const report = engine.validate(patch);
  const validationTimeMs = report.elapsed_ms ?? (performance.now() - validationStart);
  if (!report.ok) {
    process.stdout.write(JSON.stringify({
      ok: false,
      validation_failed: true,
      validation_time_ms: validationTimeMs,
      rolled_back: false,
      errors: report.errors || [report.first_error || "validation failed"],
    }));
    return;
  }

  const store = new SnapshotStore(workdir);
  const snapshot = await store.capture(patch.patch_id || "");
  try {
    await engine.apply(patch);
    store.commit(snapshot);
    process.stdout.write(JSON.stringify({
      ok: true,
      validation_failed: false,
      validation_time_ms: validationTimeMs,
      rolled_back: false,
      errors: [],
    }));
  } catch (error) {
    let rolledBack = false;
    let rollbackError = null;
    try {
      await store.restore(snapshot);
      rolledBack = true;
    } catch (restoreError) {
      rollbackError = restoreError && restoreError.message ? restoreError.message : String(restoreError);
    }
    process.stdout.write(JSON.stringify({
      ok: false,
      validation_failed: false,
      validation_time_ms: validationTimeMs,
      rolled_back: rolledBack,
      errors: [
        error && error.message ? error.message : String(error),
        ...(rollbackError ? [`Rollback failed: ${rollbackError}`] : []),
      ],
    }));
  }
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    ok: false,
    validation_failed: false,
    validation_time_ms: null,
    rolled_back: false,
    errors: [error && error.stack ? error.stack : String(error)],
  }));
  process.exitCode = 1;
});
