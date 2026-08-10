import { PatchEngine } from '../src/patch_engine';
import * as fs from 'fs';
import * as path from 'path';

describe('PatchEngine', () => {
    const testDir = path.join(__dirname, '.test_patch_engine');

    beforeEach(() => {
        if (!fs.existsSync(testDir)) {
            fs.mkdirSync(testDir);
        }
    });

    afterEach(() => {
        if (fs.existsSync(testDir)) {
            fs.rmSync(testDir, { recursive: true, force: true });
        }
    });

    test('validates valid patch', () => {
        const engine = new PatchEngine(undefined, testDir);
        
        const patch = {
            schema_version: "1.0",
            patch_id: "123e4567-e89b-12d3-a456-426614174000",
            action: "run_script",
            target: { file: "test.js" },
            changes: {
                operation: "run",
                payload: "console.log('hello');"
            }
        };

        const result = engine.validate(patch, "elevated");
        expect(result.ok).toBe(true);
    });

    test('rejects run_script without elevated trust', () => {
        const engine = new PatchEngine(undefined, testDir);
        
        const patch = {
            schema_version: "1.0",
            patch_id: "123e4567-e89b-12d3-a456-426614174000",
            action: "run_script",
            target: { file: "test.js" },
            changes: {
                operation: "run",
                payload: "console.log('hello');"
            }
        };

        const result = engine.validate(patch, "standard");
        expect(result.ok).toBe(false);
        expect(result.errors).toContain("run_script requires 'elevated' trust level");
    });

    test('rejects missing action', () => {
        const engine = new PatchEngine(undefined, testDir);
        
        const patch = {
            patch_id: "patch_1",
            changes: {}
        };

        const result = engine.validate(patch, "standard");
        expect(result.ok).toBe(false);
        // Schema validation should fail because action is missing (based on patch_schema.json)
    });
});
