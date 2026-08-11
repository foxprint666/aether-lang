"""
ai_runtime.validation.rules
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Allow-list enforcement and security constraint checks.
Runs after schema validation as the second gate.

Rule layers (in order):
  1. Operation allow-list — only permitted (action, operation) pairs pass
  2. Target path safety — no absolute paths, no path traversal (Unix + Windows)
  3. Payload safety — size limits, disallowed patterns for high-risk actions
  4. run_script trust check — requires explicit trust grant
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Load shared security rules from sdk/security_rules.json
# ---------------------------------------------------------------------------

def _load_security_rules() -> dict:
    """
    Locate and load sdk/security_rules.json.
    Searches upward from this file's location.
    Falls back to built-in defaults if not found (e.g. installed package).
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, here.parent.parent, here.parent.parent.parent,
                   here.parent.parent.parent.parent]:
        candidate = parent / "security_rules.json"
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}  # built-in fallback defined below


_RULES = _load_security_rules()

# Built-in fallback (also used when rules dict is incomplete)
_BUILTIN_BLOCKED = [
    re.compile(r"import\s+os\s*;?\s*os\s*\.\s*system", re.IGNORECASE),
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"subprocess\s*\.\s*(?:call|run|Popen|check_output)", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"exec\s*\(", re.IGNORECASE),
    re.compile(r"\bopen\s*\([^)]*['\"](?:/|\.\./)", re.IGNORECASE),
    re.compile(r"ctypes\s*\.\s*CDLL", re.IGNORECASE),
]

# Compile patterns from JSON (with fallback)
_raw_patterns = _RULES.get("blocked_payload_patterns", [])
if _raw_patterns:
    _BLOCKED_PAYLOAD_PATTERNS: list[re.Pattern] = [
        re.compile(entry["pattern"], re.IGNORECASE)
        for entry in _raw_patterns
        if isinstance(entry, dict) and "pattern" in entry
    ]
else:
    _BLOCKED_PAYLOAD_PATTERNS = _BUILTIN_BLOCKED

_SENSITIVE_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in _RULES.get("sensitive_path_patterns", [
        r"\.env", r"\.git", r"node_modules", r"secrets",
    ])
]

_MAX_PAYLOAD_BYTES: int = _RULES.get("max_payload_size_bytes", 65536)

# run_script is high-risk; requires explicit trust elevation in metadata
_RUN_SCRIPT_REQUIRES_TRUST: bool = _RULES.get("run_script_requires_trust", True)

# ---------------------------------------------------------------------------
# Allow-list: (action, operation) pairs permitted in v1
# (Python-specific; not in shared JSON because it maps to AST engine ops)
# ---------------------------------------------------------------------------

ALLOWED_OPERATIONS: dict[str, set[str]] = {
    "modify_function":  {"replace_body", "insert_before", "insert_after", "update_logic"},
    "add_function":     {"replace_body"},
    "remove_function":  {"replace_body"},   # payload ignored; target is removed
    "modify_class":     {"replace_body", "insert_before", "insert_after"},
    "update_import":    {"add_import", "remove_import"},
    "replace_block":    {"context_replace"},
    "run_script":       {"run"},
}


@dataclass
class RuleViolation:
    rule: str
    message: str


@dataclass
class RulesResult:
    """Result of allow-list and security rule checks."""
    valid: bool
    violations: list[RuleViolation] = field(default_factory=list)

    @property
    def first_violation(self) -> Optional[RuleViolation]:
        return self.violations[0] if self.violations else None

    def __bool__(self) -> bool:
        return self.valid


def check_rules(patch: dict, *, trust_level: str = "standard") -> RulesResult:
    """
    Apply all security and allow-list rules to a schema-valid patch.

    Args:
        patch: A schema-validated patch dict.
        trust_level: 'standard' (default) or 'elevated' (for run_script).

    Returns:
        RulesResult with valid=True, or a list of RuleViolation objects.
    """
    violations: list[RuleViolation] = []

    action   = patch.get("action", "")
    target   = patch.get("target", {})
    changes  = patch.get("changes", {})
    operation = changes.get("operation", "")
    payload  = changes.get("payload", "")
    metadata = patch.get("metadata", {})

    # --- Rule 1: Operation allow-list ---
    allowed = ALLOWED_OPERATIONS.get(action, set())
    if operation not in allowed:
        violations.append(RuleViolation(
            rule="operation_allow_list",
            message=(
                f"Operation '{operation}' is not permitted for action '{action}'. "
                f"Allowed: {sorted(allowed)}"
            ),
        ))

    # --- Rule 2: Target path safety ---
    file_path = target.get("file", "")
    normalized = file_path.replace("\\", "/")
    # Detect Unix absolute paths (/etc/passwd), Windows absolute paths (C:/..., \\server\...)
    is_unix_absolute    = normalized.startswith("/")
    is_windows_absolute = (
        len(normalized) >= 2 and normalized[1] == ":"          # C:\... or C:/...
        or normalized.startswith("//")                          # UNC paths \\server\share
    )
    if is_unix_absolute or is_windows_absolute:
        violations.append(RuleViolation(
            rule="target_path_absolute",
            message=f"Target file path must be relative, not absolute: '{file_path}'",
        ))
    if ".." in normalized.split("/"):
        violations.append(RuleViolation(
            rule="target_path_traversal",
            message=f"Path traversal detected in target file: '{file_path}'",
        ))
    # Check sensitive path patterns (loaded from security_rules.json)
    for sp in _SENSITIVE_PATH_PATTERNS:
        if sp.search(file_path):
            violations.append(RuleViolation(
                rule="target_path_sensitive",
                message=(
                    f"Target file '{file_path}' matches a sensitive path pattern "
                    f"(pattern: '{sp.pattern}'). Modify this file manually."
                ),
            ))
            break

    # --- Rule 3: Payload safety patterns (from security_rules.json) ---
    if payload:
        if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            violations.append(RuleViolation(
                rule="payload_too_large",
                message=(
                    f"Payload exceeds maximum size of {_MAX_PAYLOAD_BYTES} bytes. "
                    "Split into smaller patches."
                ),
            ))
        for pattern in _BLOCKED_PAYLOAD_PATTERNS:
            if pattern.search(payload):
                violations.append(RuleViolation(
                    rule="payload_blocked_pattern",
                    message=(
                        f"Payload contains a disallowed pattern: '{pattern.pattern[:60]}'. "
                        "Use the sandbox constraints instead of calling OS directly."
                    ),
                ))
                break  # one violation per category is enough

    # --- Rule 4: run_script trust elevation ---
    if action == "run_script" and _RUN_SCRIPT_REQUIRES_TRUST:
        if trust_level != "elevated":
            violations.append(RuleViolation(
                rule="run_script_trust_required",
                message=(
                    "Action 'run_script' requires trust_level='elevated'. "
                    "Call PatchEngine.validate(patch, trust_level='elevated') explicitly."
                ),
            ))

    return RulesResult(valid=len(violations) == 0, violations=violations)
