import * as fs from 'fs';
import * as path from 'path';
import * as recast from 'recast';
import * as tsParser from 'recast/parsers/typescript';

function getParser() {
    return tsParser;
}

function parseReplacementBody(payload: string): any {
    const dummyCode = `function dummy() {\n${payload}\n}`;
    try {
        const dummyAst = recast.parse(dummyCode, { parser: getParser() });
        return dummyAst.program.body[0].body;
    } catch (functionError: any) {
        const privateNames = Array.from(
            new Set(Array.from(payload.matchAll(/this\.#([A-Za-z_$][\w$]*)/g), match => match[1])),
        );
        if (privateNames.length === 0) {
            throw functionError;
        }

        const declarations = privateNames.map(name => `#${name};`).join("\n");
        const dummyClassCode = `class Dummy {\n${declarations}\ndummy() {\n${payload}\n}\n}`;
        const dummyAst = recast.parse(dummyClassCode, { parser: getParser() });
        const classBody = dummyAst.program.body[0].body.body;
        const dummyMethod = classBody.find((node: any) => node.key?.name === "dummy");
        if (!dummyMethod) {
            throw new Error("Could not extract dummy class method body");
        }
        return dummyMethod.body;
    }
}

export function applyPatch(patch: any, projectRoot: string): void {
    const target = patch.target || {};
    const action = patch.action || "";
    const filePath = path.join(projectRoot, target.file);
    
    if (!fs.existsSync(filePath)) {
        if ((action === "add_function" || action === "modify_class") && !fs.existsSync(path.dirname(filePath))) {
            fs.mkdirSync(path.dirname(filePath), { recursive: true });
            fs.writeFileSync(filePath, "", "utf-8");
        } else if (action !== "add_function") {
            throw new Error(`Target file not found: ${filePath}`);
        }
    }
    
    let source = "";
    if (fs.existsSync(filePath)) {
        source = fs.readFileSync(filePath, "utf-8");
    }
    
    if (!source.trim()) {
        source = "";
    }
    
    let ast: any;
    try {
        ast = recast.parse(source, { parser: getParser() });
    } catch (e: any) {
        throw new Error(`Failed to parse file: ${e.message}`);
    }
    
    const changes = patch.changes || {};
    
    if (action === "modify_function") {
        applyModifyFunction(ast, target, changes);
    } else if (action === "add_function") {
        applyAddFunction(ast, target, changes);
    } else if (action === "remove_function") {
        applyRemoveFunction(ast, target);
    } else if (action === "modify_class") {
        applyModifyClass(ast, target, changes);
    } else if (action === "update_import") {
        applyUpdateImport(ast, changes);
    } else if (action === "replace_block") {
        ast = applyReplaceBlock(ast, changes, source);
    } else {
        throw new Error(`Unsupported action: ${action}`);
    }
    
    const output = recast.print(ast).code;
    fs.writeFileSync(filePath, output, "utf-8");
}

function applyModifyFunction(ast: any, target: any, changes: any): void {
    const operation = changes.operation;
    const payload = changes.payload || "";
    const symbol = target.symbol;
    
    if (!symbol) {
        throw new Error("modify_function requires target.symbol");
    }
    
    if (operation === "replace_body" || operation === "update_logic") {
        let newBody: any;
        try {
            newBody = parseReplacementBody(payload);
        } catch (e: any) {
            throw new Error(`Failed to parse new function body: ${e.message}`);
        }
        
        let found = false;
        recast.visit(ast, {
            visitFunctionDeclaration(path) {
                if (path.node.id?.type === 'Identifier' && path.node.id.name === symbol) {
                    found = true;
                    path.node.body = newBody;
                    return false;
                }
                this.traverse(path);
            },
            visitFunctionExpression(path) {
                if (path.node.id?.type === 'Identifier' && path.node.id.name === symbol) {
                    found = true;
                    path.node.body = newBody;
                    return false;
                }
                this.traverse(path);
            },
            visitArrowFunctionExpression(path) {
                if (path.parentPath?.node?.type === 'VariableDeclarator' && path.parentPath.node.id?.type === 'Identifier' && path.parentPath.node.id.name === symbol) {
                    found = true;
                    path.node.body = newBody;
                    return false;
                }
                this.traverse(path);
            },
            visitClassMethod(path) {
                if (path.node.key?.type === 'Identifier' && path.node.key.name === symbol) {
                    found = true;
                    path.node.body = newBody;
                    return false;
                }
                this.traverse(path);
            }
        });
        
        if (!found) {
            throw new Error(`Function ${symbol} not found in target file`);
        }
    } else if (operation === "insert_before" || operation === "insert_after") {
        let newNodes: any[];
        try {
            const parsed = recast.parse(payload, { parser: getParser() });
            newNodes = parsed.program.body;
        } catch (e: any) {
            throw new Error(`Failed to parse payload for insert: ${e.message}`);
        }
        
        let found = false;
        recast.visit(ast, {
            visitFunctionDeclaration(path) {
                if (path.node.id?.type === 'Identifier' && path.node.id.name === symbol) {
                    found = true;
                    if (operation === "insert_before") {
                        path.insertBefore(...newNodes);
                    } else {
                        path.insertAfter(...newNodes);
                    }
                    return false;
                }
                this.traverse(path);
            },
            visitFunctionExpression(path) {
                if (path.node.id?.type === 'Identifier' && path.node.id.name === symbol) {
                    found = true;
                    let targetPath: any = path;
                    while (targetPath && targetPath.node.type !== 'VariableDeclaration') {
                        targetPath = targetPath.parentPath;
                    }
                    if (targetPath && targetPath.node.type === 'VariableDeclaration') {
                        if (operation === "insert_before") {
                            targetPath.insertBefore(...newNodes);
                        } else {
                            targetPath.insertAfter(...newNodes);
                        }
                    } else {
                        throw new Error("Cannot insert_before/after a FunctionExpression without a parent variable declaration");
                    }
                    return false;
                }
                this.traverse(path);
            },
            visitArrowFunctionExpression(path) {
                if (path.parentPath?.node?.type === 'VariableDeclarator' && path.parentPath.node.id?.type === 'Identifier' && path.parentPath.node.id.name === symbol) {
                    found = true;
                    let targetPath: any = path;
                    while (targetPath && targetPath.node.type !== 'VariableDeclaration') {
                        targetPath = targetPath.parentPath;
                    }
                    if (targetPath && targetPath.node.type === 'VariableDeclaration') {
                        if (operation === "insert_before") {
                            targetPath.insertBefore(...newNodes);
                        } else {
                            targetPath.insertAfter(...newNodes);
                        }
                    } else {
                        throw new Error("Cannot insert_before/after an ArrowFunction without a parent variable declaration");
                    }
                    return false;
                }
                this.traverse(path);
            },
            visitClassMethod(path) {
                if (path.node.key?.type === 'Identifier' && path.node.key.name === symbol) {
                    found = true;
                    if (operation === "insert_before") {
                        path.insertBefore(...newNodes);
                    } else {
                        path.insertAfter(...newNodes);
                    }
                    return false;
                }
                this.traverse(path);
            }
        });
        
        if (!found) {
            throw new Error(`Function ${symbol} not found in target file`);
        }
    } else {
        throw new Error(`Operation ${operation} not fully implemented for modify_function`);
    }
}

function applyAddFunction(ast: any, target: any, changes: any): void {
    const payload = (changes.payload || "").trim();
    let newFuncAst: any;
    try {
        newFuncAst = recast.parse(payload, { parser: getParser() });
    } catch (e: any) {
        throw new Error(`Failed to parse new function: ${e.message}`);
    }
    
    const newFuncNodes = newFuncAst.program.body;
    if (!ast.program.body) {
        ast.program.body = [];
    }
    ast.program.body.push(...newFuncNodes);
}

function applyRemoveFunction(ast: any, target: any): void {
    const symbol = target.symbol;
    if (!symbol) {
        throw new Error("remove_function requires target.symbol");
    }
    
    let removed = false;
    recast.visit(ast, {
        visitFunctionDeclaration(path) {
            if (path.node.id?.type === 'Identifier' && path.node.id.name === symbol) {
                removed = true;
                path.prune();
                return false;
            }
            this.traverse(path);
        },
        visitVariableDeclaration(path) {
            let declsToRemove = [];
            for (let i = 0; i < path.node.declarations.length; i++) {
                const decl: any = path.node.declarations[i];
                if (decl.id?.type === 'Identifier' && decl.id.name === symbol && 
                    (decl.init?.type === 'ArrowFunctionExpression' || decl.init?.type === 'FunctionExpression')) {
                    removed = true;
                    declsToRemove.push(i);
                }
            }
            if (declsToRemove.length > 0) {
                if (declsToRemove.length === path.node.declarations.length) {
                    path.prune();
                } else {
                    // Remove the specific declarators
                    // Reverse to keep indices valid
                    for (const idx of declsToRemove.reverse()) {
                        path.node.declarations.splice(idx, 1);
                    }
                }
                return false;
            }
            this.traverse(path);
        },
        visitClassMethod(path) {
            if (path.node.key?.type === 'Identifier' && path.node.key.name === symbol) {
                removed = true;
                path.prune();
                return false;
            }
            this.traverse(path);
        }
    });
    
    if (!removed) {
        throw new Error(`Function ${symbol} not found to remove`);
    }
}

function applyUpdateImport(ast: any, changes: any): void {
    const operation = changes.operation;
    const imports = changes.imports || [];
    
    if (operation === "add_import") {
        const parsedImports: any[] = [];
        for (const imp of imports) {
            try {
                const mod = recast.parse(imp, { parser: getParser() });
                parsedImports.push(...mod.program.body);
            } catch (e: any) {
                throw new Error(`Failed to parse import '${imp}': ${e.message}`);
            }
        }
        
        let insertIdx = 0;
        const body = ast.program.body || [];
        for (let i = 0; i < body.length; i++) {
            if (body[i].type === 'ImportDeclaration') {
                insertIdx = i + 1;
            }
        }
        
        body.splice(insertIdx, 0, ...parsedImports);
    } else if (operation === "remove_import") {
        const targetStrings = new Set(imports.map((i: string) => i.trim()));
        recast.visit(ast, {
            visitImportDeclaration(path) {
                const code = recast.print(path.node).code.trim();
                // Simple matching or check if any of the target strings matches
                if (targetStrings.has(code) || targetStrings.has(code + ';')) {
                    path.prune();
                    return false;
                }
                this.traverse(path);
            }
        });
    } else {
        throw new Error(`Unsupported operation for update_import: ${operation}`);
    }
}

function applyModifyClass(ast: any, target: any, changes: any): void {
    const symbol = target.symbol;
    const operation = changes.operation;
    const payload = changes.payload || "";

    if (!symbol) {
        throw new Error("modify_class requires target.symbol");
    }

    if (operation === "replace_body" || operation === "update_logic") {
        const dummyCode = `class Dummy {\n${payload}\n}`;
        let newBody: any;
        try {
            const dummyAst = recast.parse(dummyCode, { parser: getParser() });
            newBody = dummyAst.program.body[0].body;
        } catch (e: any) {
            throw new Error(`Failed to parse new class body: ${e.message}`);
        }

        let found = false;
        recast.visit(ast, {
            visitClassDeclaration(path) {
                if (path.node.id?.name === symbol) {
                    found = true;
                    path.node.body = newBody;
                    return false;
                }
                this.traverse(path);
            },
            visitClassExpression(path) {
                if (path.node.id?.name === symbol) {
                    found = true;
                    path.node.body = newBody;
                    return false;
                }
                this.traverse(path);
            }
        });

        if (!found) {
            throw new Error(`Class ${symbol} not found in target file`);
        }
    } else if (operation === "insert_before" || operation === "insert_after") {
        let newNodes: any[];
        try {
            const parsed = recast.parse(payload, { parser: getParser() });
            newNodes = parsed.program.body;
        } catch (e: any) {
            throw new Error(`Failed to parse payload for insert: ${e.message}`);
        }
        
        let found = false;
        recast.visit(ast, {
            visitClassDeclaration(path) {
                if (path.node.id?.name === symbol) {
                    found = true;
                    if (operation === "insert_before") {
                        path.insertBefore(...newNodes);
                    } else {
                        path.insertAfter(...newNodes);
                    }
                    return false;
                }
                this.traverse(path);
            },
            visitClassExpression(path) {
                if (path.node.id?.name === symbol) {
                    found = true;
                    let targetPath: any = path;
                    while (targetPath && targetPath.node.type !== 'VariableDeclaration') {
                        targetPath = targetPath.parentPath;
                    }
                    if (targetPath && targetPath.node.type === 'VariableDeclaration') {
                        if (operation === "insert_before") {
                            targetPath.insertBefore(...newNodes);
                        } else {
                            targetPath.insertAfter(...newNodes);
                        }
                    } else {
                        throw new Error("Cannot insert_before/after a ClassExpression without a parent variable declaration");
                    }
                    return false;
                }
                this.traverse(path);
            }
        });
        
        if (!found) {
            throw new Error(`Class ${symbol} not found in target file`);
        }
    } else {
        throw new Error(`Operation ${operation} not fully implemented for modify_class`);
    }
}

function applyReplaceBlock(ast: any, changes: any, source: string): any {
    const contextBefore = changes.context_before || "";
    const contextAfter = changes.context_after || "";
    const payload = changes.payload || "";

    if (!contextBefore) throw new Error("replace_block requires context_before");

    const beforeIdx = source.indexOf(contextBefore);
    if (beforeIdx === -1) throw new Error("context_before not found in source");

    let newSource = "";
    if (contextAfter) {
        const afterIdx = source.indexOf(contextAfter, beforeIdx + contextBefore.length);
        if (afterIdx === -1) throw new Error("context_after not found in source");
        newSource = source.substring(0, beforeIdx + contextBefore.length) + "\n" + payload + "\n" + source.substring(afterIdx);
    } else {
        newSource = source.substring(0, beforeIdx + contextBefore.length) + "\n" + payload;
    }

    try {
        return recast.parse(newSource, { parser: getParser() });
    } catch (e: any) {
        throw new Error(`Failed to parse file after replace_block: ${e.message}`);
    }
}
