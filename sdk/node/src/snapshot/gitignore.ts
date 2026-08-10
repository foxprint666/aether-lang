import fs from 'fs';
import path from 'path';
import ignore from 'ignore';

const ALWAYS_EXCLUDE = [
    // JS / package managers
    "node_modules", "node_modules/", ".npm", ".yarn",
    // Python virtualenvs
    "venv", "venv/", ".venv", ".venv/", "env", "env/", ".env", ".env/",
    // Version control
    ".git", ".git/", ".hg", ".hg/", ".svn", ".svn/",
    // Python cache
    "__pycache__", "__pycache__/", "*.pyc", "*.pyo", "*.pyd",
    ".mypy_cache", ".mypy_cache/", ".ruff_cache", ".ruff_cache/",
    ".pytest_cache", ".pytest_cache/", ".tox", ".tox/",
    // Build artefacts
    "dist", "dist/", "build", "build/", "*.egg-info", "*.egg-info/",
    // Rust
    "target", "target/", ".cargo", ".cargo/",
    // Native objects
    "*.o", "*.obj", "*.lib", "*.dll", "*.so", "*.dylib",
    // Our own runtime store
    ".ai_runtime", ".ai_runtime/"
];

const MAX_FILE_BYTES = 5 * 1024 * 1024;

const SKIP_DIR_NAMES = new Set([
    "node_modules", ".git", ".hg", ".svn",
    "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox",
    "dist", "build", "target", ".cargo", ".ai_runtime"
]);

export function collectSourceFiles(
    projectRoot: string,
    maxFileBytes = MAX_FILE_BYTES
): string[] {
    const alwaysIg = ignore().add(ALWAYS_EXCLUDE);
    const userIg = loadUserSpec(projectRoot);

    const results: string[] = [];
    
    function walk(dir: string) {
        let entries: fs.Dirent[];
        try {
            entries = fs.readdirSync(dir, { withFileTypes: true });
        } catch (e: any) {
            if (e.code === 'EACCES' || e.code === 'EPERM') return;
            throw e;
        }

        for (const entry of entries) {
            if (entry.isDirectory()) {
                if (SKIP_DIR_NAMES.has(entry.name)) continue;
                
                const relPath = path.relative(projectRoot, path.join(dir, entry.name)).split(path.sep).join('/');
                if (alwaysIg.ignores(relPath + '/')) continue;
                if (userIg && userIg.ignores(relPath + '/')) continue;
                
                walk(path.join(dir, entry.name));
            } else if (entry.isFile()) {
                const fullPath = path.join(dir, entry.name);
                const relPath = path.relative(projectRoot, fullPath).split(path.sep).join('/');
                
                if (alwaysIg.ignores(relPath)) continue;
                if (userIg && userIg.ignores(relPath)) continue;
                
                try {
                    const stats = fs.statSync(fullPath);
                    if (stats.size > maxFileBytes) continue;
                } catch (e) {
                    continue;
                }
                
                results.push(fullPath);
            }
        }
    }
    
    walk(projectRoot);
    return results;
}

function loadUserSpec(projectRoot: string) {
    const ig = ignore();
    let hasPatterns = false;
    for (const file of ['.gitignore', '.ai_runtimeignore']) {
        const p = path.join(projectRoot, file);
        if (fs.existsSync(p)) {
            try {
                const content = fs.readFileSync(p, 'utf8');
                ig.add(content);
                hasPatterns = true;
            } catch (e) {
                // Ignore read errors
            }
        }
    }
    return hasPatterns ? ig : null;
}
