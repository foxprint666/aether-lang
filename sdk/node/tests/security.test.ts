import { SecurityRules } from '../src/security';
import * as path from 'path';

describe('SecurityRules', () => {
    describe('isPathSafe', () => {
        const baseDir = '/app/workspace';

        it('should allow paths within base directory', () => {
            expect(SecurityRules.isPathSafe(baseDir, 'src/index.ts')).toBe(true);
            expect(SecurityRules.isPathSafe(baseDir, './package.json')).toBe(true);
        });

        it('should reject path traversal attempts', () => {
            expect(SecurityRules.isPathSafe(baseDir, '../outside.txt')).toBe(false);
            expect(SecurityRules.isPathSafe(baseDir, '../../etc/passwd')).toBe(false);
            expect(SecurityRules.isPathSafe(baseDir, '/etc/passwd')).toBe(false);
        });
    });

    describe('isAccessingSensitiveFiles', () => {
        it('should identify sensitive files', () => {
            expect(SecurityRules.isAccessingSensitiveFiles('.env')).toBe(true);
            expect(SecurityRules.isAccessingSensitiveFiles('.env.local')).toBe(true);
            expect(SecurityRules.isAccessingSensitiveFiles('.git/config')).toBe(true);
            expect(SecurityRules.isAccessingSensitiveFiles('node_modules/lodash/index.js')).toBe(true);
        });

        it('should allow normal files', () => {
            expect(SecurityRules.isAccessingSensitiveFiles('src/index.ts')).toBe(false);
            expect(SecurityRules.isAccessingSensitiveFiles('package.json')).toBe(false);
            expect(SecurityRules.isAccessingSensitiveFiles('README.md')).toBe(false);
        });
    });
});
