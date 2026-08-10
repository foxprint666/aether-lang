import { PatchSchemaValidator } from '../src/validation';
import * as path from 'path';

describe('PatchSchemaValidator', () => {
    let validator: PatchSchemaValidator;

    beforeAll(() => {
        validator = new PatchSchemaValidator();
    });

    it('should validate a correct patch', () => {
        const patch = {
            schema_version: '1.0',
            patch_id: '123e4567-e89b-12d3-a456-426614174000',
            action: 'modify_function',
            target: {
                file: 'src/index.ts',
                symbol: 'testFunction',
                symbol_type: 'function'
            },
            changes: {
                operation: 'replace_body',
                payload: 'function testFunction() {}'
            }
        };

        const result = validator.validate(patch);
        if (!result.valid) {
            console.log(result.errors);
        }
        expect(result.valid).toBe(true);
        expect(result.errors.length).toBe(0);
    });

    it('should reject a patch missing required fields', () => {
        const patch = {
            action: 'modify_function',
        };

        const result = validator.validate(patch);
        expect(result.valid).toBe(false);
        expect(result.errors.length).toBeGreaterThan(0);
    });

    it('should reject a patch with invalid action', () => {
        const patch = {
            patch_id: 'test-uuid-1234',
            action: 'invalid_action',
        };

        const result = validator.validate(patch);
        expect(result.valid).toBe(false);
    });
});
