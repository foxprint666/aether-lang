import * as fs from 'fs';
import * as path from 'path';
import { applyPatch } from '../src/ast/engine';

describe('AST Engine', () => {
    const testDir = path.join(__dirname, '.test_ast');
    const testFile = 'target.js';
    const testFilePath = path.join(testDir, testFile);

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

    test('add_function to empty file', () => {
        const patch = {
            action: 'add_function',
            target: { file: testFile },
            changes: {
                payload: 'function test() { return 42; }'
            }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('function test() { return 42; }');
    });

    test('modify_function replace_body', () => {
        fs.writeFileSync(testFilePath, 'function test() { return 0; }', 'utf-8');

        const patch = {
            action: 'modify_function',
            target: { file: testFile, symbol: 'test' },
            changes: {
                operation: 'replace_body',
                payload: 'return 42;'
            }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('return 42;');
        expect(content).not.toContain('return 0;');
    });

    test('remove_function', () => {
        fs.writeFileSync(testFilePath, 'function test() { return 42; }\nconst other = 1;', 'utf-8');

        const patch = {
            action: 'remove_function',
            target: { file: testFile, symbol: 'test' }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).not.toContain('function test() { return 42; }');
        expect(content).toContain('const other = 1;');
    });

    test('update_import add_import', () => {
        fs.writeFileSync(testFilePath, 'import a from "a";\nconst b = 1;', 'utf-8');

        const patch = {
            action: 'update_import',
            target: { file: testFile },
            changes: {
                operation: 'add_import',
                imports: ['import { b } from "b";']
            }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('import { b } from "b";');
    });

    test('modify_function with arrow function assigned to variable', () => {
        fs.writeFileSync(testFilePath, 'const test = () => { return 0; };', 'utf-8');

        const patch = {
            action: 'modify_function',
            target: { file: testFile, symbol: 'test' },
            changes: {
                operation: 'replace_body',
                payload: 'return 42;'
            }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('return 42;');
        expect(content).not.toContain('return 0;');
    });

    test('modify_function class method supports referenced private fields', () => {
        fs.writeFileSync(
            testFilePath,
            'class Queue {\n    #head;\n    clear() { return 0; }\n}',
            'utf-8',
        );

        const patch = {
            action: 'modify_function',
            target: { file: testFile, symbol: 'clear', symbol_type: 'method' },
            changes: {
                operation: 'replace_body',
                payload: 'const value = this.#head;\nthis.#head = undefined;\nreturn value;'
            }
        };

        applyPatch(patch, testDir);

        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('const value = this.#head;');
        expect(content).toContain('this.#head = undefined;');
        expect(content).not.toContain('clear() { return 0; }');
    });

    test('modify_function generator method accepts yield body', () => {
        fs.writeFileSync(
            testFilePath,
            'class Queue {\n    * drain() {\n        yield 1;\n    }\n}',
            'utf-8',
        );

        const patch = {
            action: 'modify_function',
            target: { file: testFile, symbol: 'drain', symbol_type: 'method' },
            changes: {
                operation: 'replace_body',
                payload: 'let remaining = 2;\nwhile (remaining > 0) {\n    remaining--;\n    yield remaining;\n}'
            }
        };

        applyPatch(patch, testDir);

        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('* drain()');
        expect(content).toContain('yield remaining;');
        expect(content).not.toContain('yield 1;');
    });

    test('remove_function with arrow function', () => {
        fs.writeFileSync(testFilePath, 'const test = () => { return 42; };\nconst other = 1;', 'utf-8');

        const patch = {
            action: 'remove_function',
            target: { file: testFile, symbol: 'test' }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).not.toContain('const test = () => { return 42; };');
        expect(content).toContain('const other = 1;');
    });

    test('modify_class replace_body', () => {
        fs.writeFileSync(testFilePath, 'class MyClass {\n    oldMethod() {\n        return 0;\n    }\n}', 'utf-8');

        const patch = {
            action: 'modify_class',
            target: { file: testFile, symbol: 'MyClass' },
            changes: {
                operation: 'replace_body',
                payload: 'newMethod() {\n    return 42;\n}'
            }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('newMethod() {');
        expect(content).not.toContain('oldMethod() {');
    });

    test('replace_block context_replace', () => {
        fs.writeFileSync(testFilePath, 'function test() {\n    // A comment\n    let x = 1;\n    let y = 2;\n    let z = 3;\n    return x + y + z;\n}', 'utf-8');

        const patch = {
            action: 'replace_block',
            target: { file: testFile },
            changes: {
                operation: 'context_replace',
                context_before: '    let x = 1;\n',
                context_after: '    let z = 3;\n',
                payload: '    let y = 42;'
            }
        };

        applyPatch(patch, testDir);
        
        const content = fs.readFileSync(testFilePath, 'utf-8');
        expect(content).toContain('let x = 1;');
        expect(content).toContain('let y = 42;');
        expect(content).not.toContain('let y = 2;');
        expect(content).toContain('let z = 3;');
    });
});
