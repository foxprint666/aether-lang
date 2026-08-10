import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { T3SubprocessSandbox } from '../src/sandbox';

describe('T3SubprocessSandbox', () => {
    let sandbox: T3SubprocessSandbox;
    let tempDir: string;

    beforeAll(() => {
        sandbox = new T3SubprocessSandbox();
        tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-test-'));
    });

    afterAll(() => {
        fs.rmSync(tempDir, { recursive: true, force: true });
    });

    test('should execute basic javascript', async () => {
        const payload = `console.log('hello world');`;
        const result = await sandbox.run(payload, {
            timeout_ms: 5000,
            memory_limit_mb: 128,
            allow_network: false,
            allow_filesystem: false,
            cwd: tempDir
        });

        expect(result.failed).toBe(false);
        expect(result.exit_code).toBe(0);
        expect(result.stdout).toContain('hello world');
    });

    test('should time out on infinite loop', async () => {
        const payload = `while(true) {}`;
        const result = await sandbox.run(payload, {
            timeout_ms: 1000,
            memory_limit_mb: 128,
            allow_network: false,
            allow_filesystem: false,
            cwd: tempDir
        });

        expect(result.failed).toBe(true);
        expect(result.error).toContain('timed out');
        expect(result.elapsed_ms).toBeGreaterThanOrEqual(1000);
    });

    test('should prevent unauthorized filesystem access when not allowed', async () => {
        const payload = `
            const fs = require('fs');
            const path = require('path');
            fs.writeFileSync(path.join(process.cwd(), 'test.txt'), 'secret');
        `;
        const result = await sandbox.run(payload, {
            timeout_ms: 5000,
            memory_limit_mb: 128,
            allow_network: false,
            allow_filesystem: false,
            cwd: tempDir
        });

        // It should either fail via node permission model (ERR_ACCESS_DENIED)
        // or standard Error if write outside sandbox is attempted depending on node version/flags.
        expect(result.failed).toBe(true);
        expect(result.exit_code).not.toBe(0);
        expect(
            result.error?.includes('ERR_ACCESS_DENIED') || 
            result.error?.includes('Access to this API has been restricted') ||
            result.error?.includes('EACCES') ||
            result.error?.includes('EPERM') ||
            result.stderr?.includes('ERR_ACCESS_DENIED')
        ).toBeTruthy();
    });

    test('should capture thrown errors', async () => {
        const payload = `throw new Error('Test Error');`;
        const result = await sandbox.run(payload, {
            timeout_ms: 5000,
            memory_limit_mb: 128,
            allow_network: false,
            allow_filesystem: false,
            cwd: tempDir
        });

        expect(result.failed).toBe(true);
        expect(result.error).toContain('Test Error');
    });
});
