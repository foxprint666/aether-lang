"""
ai_runtime.validation.schema
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
JSON Schema validation for AI-Safe structured patches.
Validates incoming patch dicts against patch_schema.json (Draft 2020-12).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import jsonschema
    from jsonschema import Draft202012Validator, ValidationError
    from jsonschema.validators import validator_for
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False

_SCHEMA_PATH = Path(__file__).parent / "patch_schema.json"
_COMPILED_VALIDATOR: Optional["Draft202012Validator"] = None

# UUID v4 regex — used to patch the schema's format assertion cross-platform
_UUID_PATTERN = (
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}'
    r'-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
)


def _get_validator() -> "Draft202012Validator":
    """Lazy-load and compile the JSON schema validator once."""
    global _COMPILED_VALIDATOR
    if _COMPILED_VALIDATOR is None:
        if not _JSONSCHEMA_AVAILABLE:
            raise RuntimeError(
                "jsonschema is required for patch validation. "
                "Install it with: pip install jsonschema"
            )
        with _SCHEMA_PATH.open() as f:
            schema = json.load(f)
        # Inject the UUID pattern directly into the schema so format assertion
        # works without requiring format_checker (Draft 2020-12 doesn't assert
        # format by default; pattern is the portable equivalent).
        schema["properties"]["patch_id"]["pattern"] = _UUID_PATTERN
        _COMPILED_VALIDATOR = Draft202012Validator(schema)
    return _COMPILED_VALIDATOR


@dataclass
class ValidationResult:
    """Result of a schema validation check."""
    valid: bool
    errors: list[str] = field(default_factory=list)
    error_count: int = 0

    @property
    def first_error(self) -> Optional[str]:
        return self.errors[0] if self.errors else None

    def __bool__(self) -> bool:
        return self.valid


def validate_schema(patch: dict) -> ValidationResult:
    """
    Validate a patch dict against the JSON Schema (Draft 2020-12).

    Args:
        patch: A deserialized patch dict (from JSON).

    Returns:
        ValidationResult with valid=True or a list of error messages.

    Performance target: < 5ms for typical patch sizes.
    """
    validator = _get_validator()
    errors = sorted(validator.iter_errors(patch), key=lambda e: list(e.path))
    if not errors:
        return ValidationResult(valid=True)

    messages = []
    for err in errors:
        path = " -> ".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"[{path}] {err.message}")

    return ValidationResult(valid=False, errors=messages, error_count=len(messages))


def validate_schema_from_string(patch_json: str) -> ValidationResult:
    """
    Parse and validate a raw JSON string patch.

    Args:
        patch_json: Raw JSON string.

    Returns:
        ValidationResult. Returns an error result if the JSON is malformed.
    """
    try:
        patch = json.loads(patch_json)
    except json.JSONDecodeError as e:
        return ValidationResult(
            valid=False,
            errors=[f"Invalid JSON: {e}"],
            error_count=1,
        )
    return validate_schema(patch)
