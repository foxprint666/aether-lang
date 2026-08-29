import fs from 'fs';
import path from 'path';
import { main } from '../src/cli';

describe('Node CLI', () => {
    const testDir = path.join(__dirname, '.test_cli');
    const targetFile = path.join(testDir, 'cart.js');
    const patchFile = path.join(testDir, 'patch.json');

    let stdoutSpy: jest.SpyInstance;
    let stderrSpy: jest.SpyInstance;

    beforeEach(() => {
        fs.rmSync(testDir, { recursive: true, force: true });
        fs.mkdirSync(testDir, { recursive: true });
        fs.writeFileSync(targetFile, 'function total(items) { return 0; }\n', 'utf8');
        fs.writeFileSync(
            patchFile,
            JSON.stringify({
                schema_version: '1.0',
                patch_id: '51000000-0000-4000-8000-000000000001',
                action: 'modify_function',
                target: {
                    file: 'cart.js',
                    symbol: 'total',
                    symbol_type: 'function',
                },
                changes: {
                    operation: 'replace_body',
                    payload: 'return items.reduce((sum, item) => sum + item, 0);',
                },
            }),
            'utf8',
        );
        stdoutSpy = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
        stderrSpy = jest.spyOn(process.stderr, 'write').mockImplementation(() => true);
        jest.spyOn(console, 'log').mockImplementation(() => undefined);
        jest.spyOn(console, 'error').mockImplementation(() => undefined);
    });

    afterEach(() => {
        jest.restoreAllMocks();
        fs.rmSync(testDir, { recursive: true, force: true });
    });

    test('validate accepts patch JSON', async () => {
        await expect(main(['--project', testDir, '--json', 'validate', patchFile])).resolves.toBe(0);
        expect(console.log).toHaveBeenCalledWith(expect.stringContaining('"ok": true'));
    });

    test('apply snapshots and modifies file', async () => {
        await expect(main(['--project', testDir, '--json', 'apply', patchFile])).resolves.toBe(0);
        expect(fs.readFileSync(targetFile, 'utf8')).toContain('items.reduce');

        await expect(main(['--project', testDir, 'snapshots'])).resolves.toBe(0);
        expect(console.log).toHaveBeenCalledWith(expect.stringContaining('committed'));
    });

    test('rollback restores previous file contents', async () => {
        await expect(main(['--project', testDir, '--json', 'apply', patchFile])).resolves.toBe(0);
        const snapshots = JSON.parse(fs.readFileSync(path.join(testDir, '.ai_runtime', 'snapshots.json'), 'utf8'));

        await expect(main(['--project', testDir, 'rollback', snapshots[0].snapshot_id])).resolves.toBe(0);
        expect(fs.readFileSync(targetFile, 'utf8')).toContain('return 0');
    });

    test('validate returns clean JSON for malformed patch', async () => {
        const badPatch = path.join(testDir, 'bad.json');
        fs.writeFileSync(badPatch, '{"schema_version":"1.0"}', 'utf8');

        await expect(main(['--project', testDir, '--json', 'validate', badPatch])).resolves.toBe(1);
        expect(console.log).toHaveBeenCalledWith(expect.stringContaining('"ok": false'));
    });
});
