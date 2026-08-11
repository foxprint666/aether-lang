/**
 * ae_bridge.ts — Gate 3: Semantic Bridge for Node.js SDK
 * ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
 * Mirrors the Python `ae_bridge.py` SemanticGate exactly.
 *
 * Architecture:
 *   patch dict
 *       │
 *   SemanticGate.check(patch)
 *       │ (only when ae_target is present)
 *       ▼
 *   AeSemaBridge.checkSource(source)
 *   → spawns: ae check <tmp_file> --json [--diff-impact <hash>]
 *       │
 *   BridgeResult { ok, skipped, errors, report }
 *
 * If the `ae` binary is not on PATH, Gate 3 silently skips (pass-through).
 * This preserves full backward compatibility with non-Aether repos.
 */

import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Internal helper: find a binary on PATH without external deps
// ---------------------------------------------------------------------------
function findOnPath(name: string): string {
  // On Windows, try <name>.exe too
  const candidates = os.platform() === 'win32'
    ? [name, `${name}.exe`]
    : [name];

  const dirs = (process.env['PATH'] ?? '').split(path.delimiter);
  for (const dir of dirs) {
    for (const candidate of candidates) {
      const full = path.join(dir, candidate);
      try {
        fs.accessSync(full, fs.constants.X_OK);
        return full;
      } catch { /* not found here */ }
    }
  }
  return '';
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SemaDiag {
  severity: 'error' | 'warning' | 'info';
  message: string;
  hash_hex: string;
  stability_level: number;
  suggestion?: string;
}

export interface SemaReport {
  ok: boolean;
  has_union: boolean;
  diagnostics: SemaDiag[];
  raw: Record<string, unknown>;
  elapsed_ms: number;
}

export interface BridgeResult {
  ok: boolean;
  skipped: boolean;
  errors: string[];
  report?: SemaReport;
  elapsed_ms: number;
}

// ---------------------------------------------------------------------------
// AeSemaBridge — thin subprocess wrapper
// ---------------------------------------------------------------------------

export class AeSemaBridge {
  private aeBin: string;

  constructor(aeBinary?: string) {
    if (aeBinary) {
      this.aeBin = aeBinary;
    } else {
      this.aeBin = findOnPath('ae');
    }
  }

  get available(): boolean {
    return !!this.aeBin && fs.existsSync(this.aeBin);
  }

  checkSource(source: string, filename = 'patch_target.ae'): SemaReport {
    if (!this.available) {
      throw new Error(
        "ae binary not found on PATH. Install the Aether toolchain or set AE_BINARY env var."
      );
    }

    // Write source to temp file
    const tmpFile = path.join(os.tmpdir(), `ae_bridge_${Date.now()}.ae`);
    fs.writeFileSync(tmpFile, source, 'utf8');

    const t0 = Date.now();
    let raw: Record<string, unknown> = {};

    try {
      const result = spawnSync(this.aeBin, ['check', tmpFile, '--json'], {
        encoding: 'utf8',
        timeout: 10_000,
      });

      const elapsed_ms = Date.now() - t0;

      if (result.stdout?.trim()) {
        try {
          raw = JSON.parse(result.stdout.trim());
        } catch {
          raw = { parse_error: result.stdout.slice(0, 200) };
        }
      }

      const diagnostics: SemaDiag[] = ((raw.diagnostics as any[]) ?? []).map((d: any) => ({
        severity: (d.severity ?? 'info').toLowerCase() as SemaDiag['severity'],
        message: d.message ?? '',
        hash_hex: d.hash_hex ?? '',
        stability_level: Number(d.stability_level ?? 0),
        suggestion: d.suggestion,
      }));

      const has_union = diagnostics.some(
        d => d.stability_level >= 1 || d.message.toLowerCase().includes('union')
      );
      const has_error = diagnostics.some(d => d.severity === 'error');

      return {
        ok: !has_error,
        has_union,
        diagnostics,
        raw,
        elapsed_ms,
      };
    } finally {
      try { fs.unlinkSync(tmpFile); } catch { /* ignore */ }
    }
  }

  checkFile(filePath: string): SemaReport {
    const source = fs.readFileSync(filePath, 'utf8');
    return this.checkSource(source, path.basename(filePath));
  }
}

// ---------------------------------------------------------------------------
// SemanticGate — Gate 3
// ---------------------------------------------------------------------------

export class SemanticGate {
  private bridge: AeSemaBridge;

  constructor(aeBinary?: string) {
    const bin = aeBinary ?? process.env['AE_BINARY'] ?? '';
    this.bridge = new AeSemaBridge(bin || undefined);
  }

  /**
   * Run Gate 3 on a patch.
   *
   * - No ae_target      → {ok: true, skipped: true}
   * - ae binary missing → {ok: true, skipped: true, errors: ["...skipped..."]}
   * - ae_target present → full semantic analysis
   */
  check(patch: Record<string, any>): BridgeResult {
    const t0 = Date.now();

    const aeTarget = patch['ae_target'];
    if (!aeTarget) {
      return { ok: true, skipped: true, errors: [], elapsed_ms: 0 };
    }

    if (!this.bridge.available) {
      return {
        ok: true,
        skipped: true,
        errors: [
          "SemanticGate skipped: 'ae' binary not found. " +
          "Install the Aether toolchain for hash-addressed stability checks.",
        ],
        elapsed_ms: Date.now() - t0,
      };
    }

    const replacementSrc: string | undefined = aeTarget['replacement_src'];
    const stabilityRequired: boolean = aeTarget['stability_required'] !== false;
    const nodeHash: string = aeTarget['node_hash'] ?? '';

    if (!replacementSrc) {
      return { ok: true, skipped: true, errors: [], elapsed_ms: Date.now() - t0 };
    }

    let report: SemaReport;
    try {
      report = this.bridge.checkSource(
        replacementSrc,
        `ae_patch_${nodeHash.slice(0, 8)}.ae`,
      );
    } catch (err: any) {
      return {
        ok: false,
        skipped: false,
        errors: [`SemanticGate subprocess error: ${err?.message ?? err}`],
        elapsed_ms: Date.now() - t0,
      };
    }

    const elapsed_ms = Date.now() - t0;
    const errors: string[] = [];

    if (!report.ok) {
      for (const d of report.diagnostics) {
        if (d.severity === 'error') {
          errors.push(`ae-sema error [${d.hash_hex.slice(0, 8)}]: ${d.message}`);
        }
      }
      return { ok: false, skipped: false, errors, report, elapsed_ms };
    }

    if (stabilityRequired && report.has_union) {
      for (const d of report.diagnostics.filter(d => d.stability_level >= 1)) {
        const hint = d.suggestion ? ` (hint: ${d.suggestion})` : '';
        errors.push(
          `ae-sema stability violation [${d.hash_hex.slice(0, 8)}]: ${d.message}${hint}`
        );
      }
      if (errors.length === 0) {
        errors.push(
          'SemanticGate: replacement introduces Union/dynamic types. ' +
          "Mark the function 'stable' or remove the type ambiguity."
        );
      }
      return { ok: false, skipped: false, errors, report, elapsed_ms };
    }

    return { ok: true, skipped: false, errors: [], report, elapsed_ms };
  }

  get aeAvailable(): boolean {
    return this.bridge.available;
  }
}
