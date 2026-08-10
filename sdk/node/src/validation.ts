import Ajv from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import * as fs from 'fs';
import * as path from 'path';

// Schema types based on patch_schema.json
export interface PatchRequest {
    patch_id: string;
    action: string;
    target_file?: string;
    target_files?: string[];
    [key: string]: any;
}

export class PatchSchemaValidator {
    private ajv: Ajv;
    private schema: any;

    constructor() {
        this.ajv = new Ajv({ allErrors: true, strictTypes: false });
        addFormats(this.ajv);
        
        // Load schema from python directory
        const schemaPath = path.resolve(__dirname, '../../python/ai_runtime/validation/patch_schema.json');
        if (fs.existsSync(schemaPath)) {
            const schemaContent = fs.readFileSync(schemaPath, 'utf8');
            this.schema = JSON.parse(schemaContent);
        } else {
            throw new Error(`Schema file not found at ${schemaPath}`);
        }
    }

    validate(patch: any): { valid: boolean; errors: string[] } {
        const validate = this.ajv.compile(this.schema);
        const valid = validate(patch);

        if (!valid && validate.errors) {
            return {
                valid: false,
                errors: validate.errors.map(err => `${err.instancePath} ${err.message}`.trim())
            };
        }

        return { valid: true, errors: [] };
    }
}
