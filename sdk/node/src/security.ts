import * as path from 'path';
import * as fs from 'fs';

// ---------------------------------------------------------------------------
// Shared security rules — loaded from sdk/security_rules.json
// ---------------------------------------------------------------------------

interface SecurityRulesJson {
  sensitive_path_patterns?: string[];
  blocked_payload_patterns?: Array<{ id: string; pattern: string; description: string }>;
  max_payload_size_bytes?: number;
  run_script_requires_trust?: boolean;
}

function loadSecurityRules(): SecurityRulesJson {
  // Search upward from this file's location for security_rules.json
  const candidates = [
    path.resolve(__dirname, '..', '..', 'security_rules.json'),         // sdk/security_rules.json
    path.resolve(__dirname, '..', '..', '..', 'security_rules.json'),   // repo root
    path.resolve(__dirname, 'security_rules.json'),                      // same dir
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      try {
        return JSON.parse(fs.readFileSync(candidate, 'utf-8')) as SecurityRulesJson;
      } catch {
        // ignore parse errors; fall through to defaults
      }
    }
  }
  return {};
}

const _rules = loadSecurityRules();

const _sensitivePathPatterns: RegExp[] = (
  _rules.sensitive_path_patterns ?? ['\\.env', '\\.git', 'node_modules', 'secrets']
).map(p => new RegExp(p, 'i'));

const _blockedPayloadPatterns: RegExp[] = (
  _rules.blocked_payload_patterns ?? []
).map(e => new RegExp(e.pattern, 'i'));

const _maxPayloadBytes: number = _rules.max_payload_size_bytes ?? 65536;

// ---------------------------------------------------------------------------
// SecurityRules
// ---------------------------------------------------------------------------

export class SecurityRules {
  /**
   * Checks if a target path is within the allowed base directory to prevent
   * path traversal attacks.
   */
  static isPathSafe(baseDir: string, targetPath: string): boolean {
    const resolvedBase = path.resolve(baseDir);
    const resolvedTarget = path.resolve(baseDir, targetPath);
    return (
      resolvedTarget.startsWith(resolvedBase + path.sep) ||
      resolvedTarget === resolvedBase
    );
  }

  /**
   * Checks if the patch requests access to sensitive files (e.g., .env, .git).
   * Patterns are loaded from sdk/security_rules.json → sensitive_path_patterns.
   */
  static isAccessingSensitiveFiles(targetPath: string): boolean {
    return _sensitivePathPatterns.some(pattern => pattern.test(targetPath));
  }

  /**
   * Validates if a file exists before allowing modification.
   */
  static ensureFileExists(filePath: string): boolean {
    return fs.existsSync(filePath) && fs.statSync(filePath).isFile();
  }

  /**
   * Checks if a payload contains blocked patterns.
   * Patterns are loaded from sdk/security_rules.json → blocked_payload_patterns.
   * Returns the first matching pattern description, or null if safe.
   */
  static firstBlockedPayloadPattern(payload: string): string | null {
    for (const pattern of _blockedPayloadPatterns) {
      if (pattern.test(payload)) {
        return pattern.source;
      }
    }
    return null;
  }

  /**
   * Checks if a payload exceeds the maximum allowed size.
   */
  static isPayloadTooLarge(payload: string): boolean {
    return Buffer.byteLength(payload, 'utf-8') > _maxPayloadBytes;
  }

  /**
   * Maximum payload size in bytes (from security_rules.json).
   */
  static get maxPayloadBytes(): number {
    return _maxPayloadBytes;
  }
}
