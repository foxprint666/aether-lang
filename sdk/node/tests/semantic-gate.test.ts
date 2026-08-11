/**
 * tests/semantic-gate.test.ts
 * ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
 * Phase B/C2: SemanticGate Node.js parity tests.
 *
 * Like the Python suite, tests are split into:
 *   - No-ae-binary group (run everywhere)
 *   - With-ae-binary group (auto-skipped when ae is not on PATH)
 */

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as childProcess from 'child_process';

import { SemanticGate, AeSemaBridge, BridgeResult } from '../src/ae_bridge';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function aeAvailable(): boolean {
  const dirs = (process.env['PATH'] ?? '').split(path.delimiter);
  for (const dir of dirs) {
    for (const name of ['ae', 'ae.exe']) {
      const full = path.join(dir, name);
      try { fs.accessSync(full, fs.constants.X_OK); return true; } catch {}
    }
  }
  return false;
}

const AE_AVAILABLE = aeAvailable();
const skipIfNoAe = AE_AVAILABLE ? describe : describe.skip;

// A minimal valid patch (no ae_target)
const BARE_PATCH = {
  schema_version: '1.0',
  patch_id: '00000000-0000-4000-8000-000000000001',
  action: 'modify_function',
  target: { file: 'src/lib.ae', symbol: 'compute', symbol_type: 'function' },
  changes: { operation: 'replace_body', payload: 'fn compute() -> i32 { 42 }' },
};

const STABLE_AE = `
fn add(a: i32, b: i32) -> i32 {
    a + b
}
fn main() {
    let x: i32 = add(1, 2);
}
`.trim();

const UNSTABLE_AE = `
fn maybe(flag: bool) -> auto {
    if flag { 1 } else { "hello" }
}
fn main() {
    let x = maybe(true);
}
`.trim();

// ---------------------------------------------------------------------------
// Group 1 — No ae binary required
// ---------------------------------------------------------------------------

describe('SemanticGate — no ae binary', () => {
  test('patch without ae_target is skipped (ok=true, skipped=true)', () => {
    const gate = new SemanticGate('/nonexistent/ae');
    const result = gate.check(BARE_PATCH);
    expect(result.ok).toBe(true);
    expect(result.skipped).toBe(true);
    expect(result.report).toBeUndefined();
  });

  test('ae_target without replacement_src is skipped', () => {
    const gate = new SemanticGate('/nonexistent/ae');
    const patch = {
      ...BARE_PATCH,
      ae_target: { node_hash: 'a'.repeat(64) },
    };
    const result = gate.check(patch);
    expect(result.ok).toBe(true);
    expect(result.skipped).toBe(true);
  });

  test('missing binary skips with a warning in errors', () => {
    const gate = new SemanticGate('/nonexistent/ae');
    const patch = {
      ...BARE_PATCH,
      ae_target: {
        node_hash: 'b'.repeat(64),
        replacement_src: STABLE_AE,
      },
    };
    const result = gate.check(patch);
    expect(result.ok).toBe(true);
    expect(result.skipped).toBe(true);
    expect(result.errors.some(e => e.toLowerCase().includes('not found'))).toBe(true);
  });

  test('BridgeResult with ok=false has non-empty errors', () => {
    const r: BridgeResult = { ok: false, skipped: false, errors: ['sema error: foo'], elapsed_ms: 0 };
    expect(r.ok).toBe(false);
    expect(r.errors.length).toBeGreaterThan(0);
  });

  test('aeAvailable property reflects binary state', () => {
    const gate = new SemanticGate('/nonexistent/ae');
    expect(gate.aeAvailable).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Group 2 — Live ae binary required
// ---------------------------------------------------------------------------

skipIfNoAe('SemanticGate — with ae binary', () => {
  test('AeSemaBridge.available is true when ae is on PATH', () => {
    const bridge = new AeSemaBridge();
    expect(bridge.available).toBe(true);
  });

  test('stable source → ok=true, has_union=false', () => {
    const bridge = new AeSemaBridge();
    const report = bridge.checkSource(STABLE_AE);
    expect(report.ok).toBe(true);
    expect(report.has_union).toBe(false);
    expect(report.elapsed_ms).toBeGreaterThan(0);
  });

  // TODO: ae-sema does not yet emit Union diagnostics for 'auto' return branches.
  // When ae-sema is enhanced to detect Union types from mixed-branch returns,
  // enable this test: unstable source → has_union=true or !ok
  test.todo('unstable source → has_union=true or !ok (requires ae-sema union detection)');

  test('gate passes stable patch', () => {
    const gate = new SemanticGate();
    const patch = {
      ...BARE_PATCH,
      ae_target: {
        node_hash: 'a'.repeat(64),
        replacement_src: STABLE_AE,
        stability_required: true,
      },
    };
    const result = gate.check(patch);
    expect(result.ok).toBe(true);
    expect(result.skipped).toBe(false);
    expect(result.report).toBeDefined();
    expect(result.report!.ok).toBe(true);
  });

  // TODO: ae-sema does not yet emit Union diagnostics for 'auto' return branches.
  // When ae-sema union detection is ready, enable this test.
  test.todo('gate rejects unstable patch when stability_required=true (requires ae-sema union detection)');

  test('gate allows unstable when stability_required=false', () => {
    const gate = new SemanticGate();
    const patch = {
      ...BARE_PATCH,
      ae_target: {
        node_hash: 'd'.repeat(64),
        replacement_src: UNSTABLE_AE,
        stability_required: false,
      },
    };
    const result = gate.check(patch);
    // If ae raises hard errors → reject; if only union → allowed
    if (result.report?.ok) {
      expect(result.ok).toBe(true);
    }
  });

  test('gate rejects malformed Aether source', () => {
    const gate = new SemanticGate();
    const patch = {
      ...BARE_PATCH,
      ae_target: {
        node_hash: 'e'.repeat(64),
        replacement_src: 'fn broken( { INVALID AETHER SOURCE }',
        stability_required: true,
      },
    };
    const result = gate.check(patch);
    expect(result.ok).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
  });
});
