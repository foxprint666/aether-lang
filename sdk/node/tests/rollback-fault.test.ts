/**
 * Phase A3 — Fault-Injection & Rollback Robustness Tests (Node.js)
 *
 * Tests:
 *   - File is restored after execution fails (exit code 1 / 137)
 *   - File is restored after exception thrown during patch apply
 *   - New files created by patch are removed on rollback
 *   - Calling restore() twice is idempotent (no corruption)
 *   - Corrupt archive causes restore() to throw, not silently pass
 *
 * Run with:
 *   npm test -- rollback-fault
 */

import fs from 'fs';
import path from 'path';
import { SnapshotStore } from '../src';

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function setupProject(dir: string): void {
    fs.mkdirSync(path.join(dir, 'src'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'src', 'main.ts'), "export function main() { return 'original'; }\n");
    fs.writeFileSync(path.join(dir, 'src', 'utils.ts'), "export const CONSTANT = 42;\n");
    fs.writeFileSync(path.join(dir, 'README.md'), '# Project\n');
}

function readFile(dir: string, rel: string): string {
    return fs.readFileSync(path.join(dir, rel), 'utf-8');
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('Rollback fault injection', () => {
    const testDir = path.join(__dirname, 'test_rollback_fault_env');
    let store: SnapshotStore;

    beforeEach(() => {
        if (fs.existsSync(testDir)) fs.rmSync(testDir, { recursive: true, force: true });
        fs.mkdirSync(testDir, { recursive: true });
        setupProject(testDir);
        store = new SnapshotStore(testDir);
    });

    afterEach(() => {
        if (fs.existsSync(testDir)) fs.rmSync(testDir, { recursive: true, force: true });
    });

    // ── T1: Restore after non-zero exit ────────────────────────────────────

    test('file is restored after sandbox exit code 1', async () => {
        const original = readFile(testDir, 'src/main.ts');
        const handle = await store.capture('patch-fault-1');

        // Simulate patch modifying file
        fs.writeFileSync(path.join(testDir, 'src', 'main.ts'), "CORRUPTED\n");

        // Simulate execution failure → restore
        await store.restore(handle);

        expect(readFile(testDir, 'src/main.ts')).toBe(original);
    });

    test('file is restored after sandbox exit code 137 (SIGKILL)', async () => {
        const original = readFile(testDir, 'src/utils.ts');
        const handle = await store.capture('patch-fault-137');

        fs.writeFileSync(path.join(testDir, 'src', 'utils.ts'), "export const CONSTANT = 'KILLED';\n");

        await store.restore(handle);

        expect(readFile(testDir, 'src/utils.ts')).toBe(original);
    });

    // ── T2: Restore after exception mid-apply ──────────────────────────────

    test('both files restored after exception during apply', async () => {
        const originalMain = readFile(testDir, 'src/main.ts');
        const originalUtils = readFile(testDir, 'src/utils.ts');

        const handle = await store.capture('patch-fault-exc');

        // Partially corrupt
        fs.writeFileSync(path.join(testDir, 'src', 'main.ts'), '// HALF APPLIED\n');

        // Simulate exception handling
        try {
            throw new Error('Patch apply raised mid-way: invalid AST node');
        } catch {
            await store.restore(handle);
        }

        expect(readFile(testDir, 'src/main.ts')).toBe(originalMain);
        expect(readFile(testDir, 'src/utils.ts')).toBe(originalUtils);
    });

    test('new file created by patch is removed on rollback', async () => {
        const handle = await store.capture('patch-fault-newfile');

        const newFile = path.join(testDir, 'src', 'new_module.ts');
        fs.writeFileSync(newFile, 'export function newFn() {}\n');
        expect(fs.existsSync(newFile)).toBe(true);

        await store.restore(handle);

        expect(fs.existsSync(newFile)).toBe(false);
    });

    // ── T3: Idempotency ────────────────────────────────────────────────────

    test('calling restore() twice is idempotent', async () => {
        const original = readFile(testDir, 'src/main.ts');
        const handle = await store.capture('patch-fault-idem');

        fs.writeFileSync(path.join(testDir, 'src', 'main.ts'), 'CORRUPTED\n');

        await store.restore(handle);
        expect(readFile(testDir, 'src/main.ts')).toBe(original);

        // Second restore must not throw or corrupt
        await store.restore(handle);
        expect(readFile(testDir, 'src/main.ts')).toBe(original);
    });

    // ── T4: Archive corruption ─────────────────────────────────────────────

    test('restore with corrupt archive throws rather than silently passing', async () => {
        const handle = await store.capture('patch-fault-corrupt');

        // Truncate the archive
        const archivePath = handle.path!;
        fs.writeFileSync(archivePath, Buffer.from('CORRUPT_DATA_TRUNCATED'));

        await expect(store.restore(handle)).rejects.toThrow();
    });
});
