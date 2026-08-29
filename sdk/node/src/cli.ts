#!/usr/bin/env node

import fs from 'fs';
import path from 'path';
import { PatchEngine } from './patch_engine';
import { SnapshotStore } from './snapshot/store';

type CliOptions = {
    project: string;
    json: boolean;
    trustLevel: string;
};

export async function main(argv = process.argv.slice(2)): Promise<number> {
    const { command, rest, options } = parseArgs(argv);

    if (!command || command === 'help' || command === '--help' || command === '-h') {
        printHelp();
        return command ? 0 : 1;
    }

    try {
        if (command === 'validate') {
            const patchPath = requirePatchPath(rest);
            const patch = loadPatch(patchPath);
            const engine = new PatchEngine(undefined, options.project);
            const report = engine.validate(patch, options.trustLevel);
            emit(options, {
                ok: report.ok,
                patch_id: report.patch_id,
                elapsed_ms: round(report.elapsed_ms),
                errors: report.errors,
            }, report.ok ? `OK: patch ${report.patch_id || '<unknown>'} passed validation` : `REJECTED: ${report.first_error || 'validation failed'}`);
            return report.ok ? 0 : 1;
        }

        if (command === 'apply') {
            const patchPath = requirePatchPath(rest);
            const patch = loadPatch(patchPath);
            const engine = new PatchEngine(undefined, options.project);
            const store = new SnapshotStore(options.project);
            const report = engine.validate(patch, options.trustLevel);
            if (!report.ok) {
                emit(options, {
                    ok: false,
                    patch_id: report.patch_id,
                    snapshot_id: null,
                    rolled_back: false,
                    elapsed_ms: round(report.elapsed_ms),
                    errors: report.errors,
                }, `REJECTED: ${report.first_error || 'validation failed'}`);
                return 1;
            }

            const started = performance.now();
            const handle = await store.capture(patch.patch_id || '');
            try {
                await engine.apply(patch);
                store.commit(handle);
                emit(options, {
                    ok: true,
                    patch_id: patch.patch_id,
                    snapshot_id: handle.snapshot_id,
                    rolled_back: false,
                    elapsed_ms: round(performance.now() - started),
                    errors: [],
                }, `APPLIED: patch ${patch.patch_id || '<unknown>'}\nSnapshot: ${handle.snapshot_id}`);
                return 0;
            } catch (error: any) {
                await store.restore(handle);
                emit(options, {
                    ok: false,
                    patch_id: patch.patch_id,
                    snapshot_id: handle.snapshot_id,
                    rolled_back: true,
                    elapsed_ms: round(performance.now() - started),
                    errors: [error?.message || String(error)],
                }, `FAILED: ${error?.message || String(error)}\nRolled back to pre-apply snapshot.`);
                return 1;
            }
        }

        if (command === 'snapshots') {
            const store = new SnapshotStore(options.project);
            const snapshots = store.listSnapshots();
            if (options.json) {
                console.log(JSON.stringify(snapshots, null, 2));
            } else if (snapshots.length === 0) {
                console.log('No snapshots found.');
            } else {
                for (const snapshot of snapshots) {
                    const createdAt = new Date(snapshot.created_at * 1000).toISOString();
                    const sizeMb = (snapshot.archive_size_bytes / (1024 * 1024)).toFixed(2);
                    console.log(`${snapshot.snapshot_id} | ${createdAt} | Status: ${snapshot.status.padEnd(12)} | ${sizeMb} MB`);
                }
            }
            return 0;
        }

        if (command === 'rollback') {
            const snapshotId = rest[0];
            if (!snapshotId) {
                throw new Error('rollback requires <snapshot-id>');
            }
            const store = new SnapshotStore(options.project);
            const handle = store.load(snapshotId);
            if (!handle) {
                throw new Error(`Snapshot ${snapshotId} not found.`);
            }
            await store.restore(handle);
            emit(options, { ok: true, snapshot_id: snapshotId }, `Restored snapshot ${snapshotId}`);
            return 0;
        }

        throw new Error(`Unknown command: ${command}`);
    } catch (error: any) {
        if (options.json) {
            console.log(JSON.stringify({ ok: false, errors: [error?.message || String(error)] }, null, 2));
        } else {
            console.error(`Error: ${error?.message || String(error)}`);
        }
        return 2;
    }
}

function parseArgs(argv: string[]): { command: string | undefined; rest: string[]; options: CliOptions } {
    const rest: string[] = [];
    const options: CliOptions = {
        project: process.cwd(),
        json: false,
        trustLevel: 'standard',
    };

    let command: string | undefined;
    for (let i = 0; i < argv.length; i++) {
        const arg = argv[i];
        if (arg === '--project') {
            options.project = path.resolve(requireValue(argv, ++i, '--project'));
        } else if (arg === '--json') {
            options.json = true;
        } else if (arg === '--trust-level') {
            options.trustLevel = requireValue(argv, ++i, '--trust-level');
        } else if (!command) {
            command = arg;
        } else {
            rest.push(arg);
        }
    }

    return { command, rest, options };
}

function requireValue(argv: string[], index: number, flag: string): string {
    const value = argv[index];
    if (!value) {
        throw new Error(`${flag} requires a value`);
    }
    return value;
}

function requirePatchPath(rest: string[]): string {
    const patchPath = rest[0];
    if (!patchPath) {
        throw new Error('patch JSON path is required');
    }
    return patchPath;
}

function loadPatch(patchPath: string): any {
    return JSON.parse(fs.readFileSync(path.resolve(patchPath), 'utf8'));
}

function emit(options: CliOptions, payload: unknown, text: string): void {
    if (options.json) {
        console.log(JSON.stringify(payload, null, 2));
    } else {
        const stream = String((payload as any).ok) === 'false' ? process.stderr : process.stdout;
        stream.write(`${text}\n`);
    }
}

function round(value: number): number {
    return Math.round(value * 100) / 100;
}

function printHelp(): void {
    console.log(`Aether Node.js Runtime CLI

Usage:
  aether-js [--project <dir>] [--json] validate <patch.json>
  aether-js [--project <dir>] [--json] apply <patch.json>
  aether-js [--project <dir>] [--json] snapshots
  aether-js [--project <dir>] [--json] rollback <snapshot-id>

Options:
  --project <dir>          Project root directory
  --json                   Emit machine-readable JSON
  --trust-level <level>    standard or elevated
`);
}

if (require.main === module) {
    main().then((code) => process.exit(code));
}
