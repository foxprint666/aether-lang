import { PatchSchemaValidator } from './validation';
import { SecurityRules } from './security';
import { T3SubprocessSandbox } from './sandbox';
import { applyPatch } from './ast/engine';
import { ExecutionResult } from './types';
import * as path from 'path';

export interface ValidationReport {
    ok: boolean;
    schema_result: { valid: boolean; errors: string[] };
    rules_result: { valid: boolean; errors: string[] };
    elapsed_ms: number;
    patch_id?: string;
    errors: string[];
    first_error?: string;
}

export class PatchEngine {
    private sandbox?: T3SubprocessSandbox;
    private projectRoot: string;
    private appliedCount = 0;
    private rejectedCount = 0;
    private validator: PatchSchemaValidator;

    constructor(sandbox?: T3SubprocessSandbox, projectRoot?: string) {
        this.sandbox = sandbox;
        this.projectRoot = projectRoot || process.cwd();
        this.validator = new PatchSchemaValidator();
    }

    private getSandbox(): T3SubprocessSandbox {
        if (!this.sandbox) {
            this.sandbox = new T3SubprocessSandbox();
        }
        return this.sandbox;
    }

    validate(patch: any | string, trustLevel: string = "standard"): ValidationReport {
        const t0 = performance.now();
        let parsedPatch: any;

        if (typeof patch === 'string') {
            try {
                parsedPatch = JSON.parse(patch);
            } catch (e: any) {
                return {
                    ok: false,
                    schema_result: { valid: false, errors: [`Invalid JSON: ${e.message}`] },
                    rules_result: { valid: false, errors: [] },
                    elapsed_ms: performance.now() - t0,
                    errors: [`Invalid JSON: ${e.message}`],
                    first_error: `Invalid JSON: ${e.message}`
                };
            }
        } else {
            parsedPatch = patch;
        }

        const schemaResult = this.validator.validate(parsedPatch);
        
        if (!schemaResult.valid) {
            this.rejectedCount++;
            return {
                ok: false,
                schema_result: schemaResult,
                rules_result: { valid: false, errors: [] },
                elapsed_ms: performance.now() - t0,
                patch_id: parsedPatch?.patch_id,
                errors: schemaResult.errors,
                first_error: schemaResult.errors[0]
            };
        }

        // Check Rules (Gate 2)
        const rulesResult = this.checkRules(parsedPatch, trustLevel);
        const ok = rulesResult.valid;
        
        if (!ok) {
            this.rejectedCount++;
        }

        const errors = [...schemaResult.errors, ...rulesResult.errors];

        return {
            ok,
            schema_result: schemaResult,
            rules_result: rulesResult,
            elapsed_ms: performance.now() - t0,
            patch_id: parsedPatch?.patch_id,
            errors,
            first_error: errors[0]
        };
    }

    private checkRules(patch: any, trustLevel: string): { valid: boolean; errors: string[] } {
        const errors: string[] = [];
        
        const action = patch.action;
        if (action === "run_script" && trustLevel !== "elevated") {
            errors.push("run_script requires 'elevated' trust level");
        }

        const targetFile = patch.target?.file;
        if (targetFile) {
            if (!SecurityRules.isPathSafe(this.projectRoot, targetFile)) {
                errors.push(`Path traversal detected: ${targetFile}`);
            }
            if (SecurityRules.isAccessingSensitiveFiles(targetFile)) {
                errors.push(`Access to sensitive file denied: ${targetFile}`);
            }
        }
        
        return { valid: errors.length === 0, errors };
    }

    async apply(patch: any): Promise<ExecutionResult | void> {
        const action = patch.action;
        const target = patch.target || {};

        if (!action || (!target.file && action !== "run_script")) {
            throw new Error("apply() received a patch missing 'action' or 'target.file'");
        }

        if (action === 'run_script') {
            const result = await this.applyRunScript(patch);
            this.appliedCount++;
            return result;
        }

        // All other actions dispatch to AST handler
        applyPatch(patch, this.projectRoot);
        this.appliedCount++;
    }

    async process(patch: any | string, trustLevel: string = "standard"): Promise<ValidationReport & { executionResult?: ExecutionResult }> {
        const report = this.validate(patch, trustLevel);
        
        let executionResult: ExecutionResult | undefined;
        if (report.ok) {
            const parsedPatch = typeof patch === 'string' ? JSON.parse(patch) : patch;
            const res = await this.apply(parsedPatch);
            if (res) {
                executionResult = res;
            }
        }
        
        return { ...report, executionResult };
    }

    get stats() {
        return {
            applied: this.appliedCount,
            rejected: this.rejectedCount,
            total: this.appliedCount + this.rejectedCount
        };
    }

    private async applyRunScript(patch: any): Promise<ExecutionResult> {
        const changes = patch.changes || {};
        const payload = changes.payload || "";
        const constraints = patch.constraints || {};
        const patchId = patch.patch_id || "";

        const sb = this.getSandbox();
        return await sb.run(payload, {
            timeout_ms: constraints.timeout_ms || 5000,
            memory_limit_mb: constraints.memory_limit_mb || 128,
            allow_network: constraints.allow_network || false,
            allow_filesystem: constraints.allow_filesystem || false,
            cwd: this.projectRoot
        });
    }
}
