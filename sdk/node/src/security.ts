import * as path from 'path';
import * as fs from 'fs';

export class SecurityRules {
    /**
     * Checks if a target path is within the allowed base directory to prevent path traversal.
     */
    static isPathSafe(baseDir: string, targetPath: string): boolean {
        // Resolve absolute paths
        const resolvedBase = path.resolve(baseDir);
        const resolvedTarget = path.resolve(baseDir, targetPath);

        // Check if the target is within the base directory
        if (!resolvedTarget.startsWith(resolvedBase + path.sep) && resolvedTarget !== resolvedBase) {
            return false;
        }

        return true;
    }

    /**
     * Checks if the patch requests access to sensitive files (e.g., .env, .git)
     */
    static isAccessingSensitiveFiles(targetPath: string): boolean {
        const sensitivePatterns = [
            /\.env/i,
            /\.git/i,
            /node_modules/i,
            /package-lock\.json/i, // Or should this be allowed? Let's say we don't allow modifying package-lock manually
            /secrets/i,
        ];

        return sensitivePatterns.some(pattern => pattern.test(targetPath));
    }

    /**
     * Validates if a file exists before allowing modification.
     */
    static ensureFileExists(filePath: string): boolean {
        return fs.existsSync(filePath) && fs.statSync(filePath).isFile();
    }
}
