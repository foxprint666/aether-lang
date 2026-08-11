"""
ai_runtime.validation package

Gate 1 — Schema validation  (validate_schema / ValidationResult)
Gate 2 — Security rules     (check_rules / RulesResult)
Gate 3 — Semantic Bridge    (SemanticGate / BridgeResult)  ← Phase B
"""
from .schema import validate_schema, validate_schema_from_string, ValidationResult
from .rules import check_rules, RulesResult
from .ae_bridge import SemanticGate, BridgeResult, SemaReport, AeSemaBridge

__all__ = [
    # Gate 1
    "validate_schema",
    "validate_schema_from_string",
    "ValidationResult",
    # Gate 2
    "check_rules",
    "RulesResult",
    # Gate 3
    "SemanticGate",
    "BridgeResult",
    "SemaReport",
    "AeSemaBridge",
]
