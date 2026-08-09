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

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Allow-list: (action, operation) pairs that are permitted in v1
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

# Patterns that are NEVER allowed in payload content regardless of action.
# These are defense-in-depth; the sandbox provides the primary containment.
_BLOCKED_PAYLOAD_PATTERNS: list[re.Pattern] = [
    re.compile(r"import\s+os\s*;?\s*os\s*\.\s*system", re.IGNORECASE),
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"subprocess\s*\.\s*(?:call|run|Popen|check_output)", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"exec\s*\(", re.IGNORECASE),
    re.compile(r"\bopen\s*\([^)]*['\"](?:/|\.\./)", re.IGNORECASE),  # open("/..." or open("../...")
]

# run_script is high-risk; requires explicit trust elevation in metadata
_RUN_SCRIPT_REQUIRES_TRUST = True


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

    # --- Rule 3: Payload safety patterns ---
    if payload:
        for pattern in _BLOCKED_PAYLOAD_PATTERNS:
            if pattern.search(payload):
                violations.append(RuleViolation(
                    rule="payload_blocked_pattern",
                    message=(
                        f"Payload contains a disallowed pattern: '{pattern.pattern[:60]}...'. "
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
