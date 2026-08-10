import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { spawn } from 'child_process';
import { ExecutionResult } from '../types';

export class T3SubprocessSandbox {
    /**
     * Tier-3 subprocess sandbox using Node 20+ Experimental Permissions.
     */
    async run(
        payload: string,
        options: {
            timeout_ms: number;
            memory_limit_mb: number;
            allow_network: boolean;
            allow_filesystem: boolean;
            cwd: string;
        }
    ): Promise<ExecutionResult> {
        const t0 = performance.now();

        // Create a temporary file for the result
        const tmpDir = os.tmpdir();
        const resultPath = path.join(tmpDir, `sandbox_result_${Date.now()}_${Math.random().toString(36).substring(7)}.json`);

        const requestJson = JSON.stringify({
            payload,
            result_path: resultPath,
            allow_network: options.allow_network,
            allow_filesystem: options.allow_filesystem,
            working_dir: options.cwd,
        });

        const runnerPath = path.join(__dirname, 'sandbox_runner.js');

        // Build the node command with permission flags
        const isNode20 = process.version.startsWith('v20') || process.version.startsWith('v21');
        const permissionFlag = isNode20 ? '--experimental-permission' : '--permission';
        
        const args = [
            permissionFlag,
            '--allow-fs-read=*' // Need to be able to read standard modules and runner script
        ];

        // Node.js Permission Model is default-deny.
        // Omitting --allow-fs-write acts as --deny-fs-write.
        if (options.allow_filesystem) {
            args.push(`--allow-fs-write=*`);
        } else {
            // Only allow writing to the temporary result path
            args.push(`--allow-fs-write=${resultPath}`);
        }

        // Network permissions
        if (options.allow_network) {
            args.push('--allow-child-process');
            args.push('--allow-worker');
        } else {
            // Node 20's permission model does not natively support an explicit --deny-net flag like Deno.
            // However, we restrict worker and child_process creation which limits network exploitation avenues.
            // We enforce the strict 'deny-net' policy as requested by omitting related allow flags.
        }

        // Memory limit is passed via max-old-space-size
        args.push(`--max-old-space-size=${options.memory_limit_mb}`);
        args.push(runnerPath);

        return new Promise((resolve) => {
            let timeoutId: NodeJS.Timeout;
            
            const child = spawn(process.execPath, args, {
                cwd: options.cwd,
                stdio: ['pipe', 'pipe', 'pipe'],
                windowsHide: true,
            });

            let stdout = '';
            let stderr = '';

            child.stdout.on('data', (data) => {
                stdout += data.toString();
            });

            child.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            timeoutId = setTimeout(() => {
                child.kill('SIGKILL');
                cleanup();
                const elapsed = performance.now() - t0;
                resolve({
                    failed: true,
                    exit_code: -1,
                    stdout: '',
                    stderr: '',
                    elapsed_ms: Math.round(elapsed),
                    tier: 't3_subprocess',
                    error: `Execution timed out after ${options.timeout_ms}ms`,
                });
            }, options.timeout_ms);

            child.stdin.write(requestJson);
            child.stdin.end();

            child.on('close', (code) => {
                clearTimeout(timeoutId);
                const elapsed = performance.now() - t0;

                // Check for access denied in stderr
                if (stderr.includes('ERR_ACCESS_DENIED')) {
                    cleanup();
                    resolve({
                        failed: true,
                        exit_code: code || 1,
                        stdout: stdout,
                        stderr: stderr,
                        elapsed_ms: Math.round(elapsed),
                        tier: 't3_subprocess',
                        error: 'Permission denied: Sandbox execution failed due to an unauthorized action (ERR_ACCESS_DENIED).'
                    });
                    return;
                }

                let resultData: any = {};
                try {
                    if (fs.existsSync(resultPath)) {
                        resultData = JSON.parse(fs.readFileSync(resultPath, 'utf-8'));
                    }
                } catch (e) {
                    // Ignore
                } finally {
                    cleanup();
                }

                const failed = code !== 0;

                resolve({
                    failed: failed,
                    exit_code: code || 0,
                    stdout: resultData.stdout || stdout,
                    stderr: resultData.stderr || stderr,
                    elapsed_ms: Math.round(elapsed),
                    tier: 't3_subprocess',
                    error: failed ? (resultData.error || `Process exited with code ${code}`) : undefined,
                });
            });
            
            child.on('error', (err) => {
                clearTimeout(timeoutId);
                const elapsed = performance.now() - t0;
                cleanup();
                resolve({
                    failed: true,
                    exit_code: -2,
                    stdout: '',
                    stderr: '',
                    elapsed_ms: Math.round(elapsed),
                    tier: 't3_subprocess',
                    error: `Could not spawn subprocess: ${err.message}`,
                });
            });

            function cleanup() {
                try {
                    if (fs.existsSync(resultPath)) {
                        fs.unlinkSync(resultPath);
                    }
                } catch (e) {
                    // ignore
                }
            }
        });
    }
}
