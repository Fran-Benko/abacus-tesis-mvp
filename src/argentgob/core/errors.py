"""
Catálogo único de reason codes y error codes del MVP.
INV-15: los enums desconocidos fallan de forma segura.
"""
from enum import Enum


class ReasonCode(str, Enum):
    """Códigos estables y legibles por máquina que explican un veredicto."""

    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    INJECTION_PATTERN_DETECTED = "INJECTION_PATTERN_DETECTED"
    TOPIC_NOT_FINANCIAL = "TOPIC_NOT_FINANCIAL"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    PAYLOAD_LIMIT_EXCEEDED = "PAYLOAD_LIMIT_EXCEEDED"
    SENSITIVE_DATA_FOUND = "SENSITIVE_DATA_FOUND"
    ALLOWED = "ALLOWED"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    TOOL_PROVIDER_UNAVAILABLE = "TOOL_PROVIDER_UNAVAILABLE"
    INTERNAL_GOVERNANCE_ERROR = "INTERNAL_GOVERNANCE_ERROR"
    # R2 — Frontera gobernada.
    POLICY_CONFLICT = "POLICY_CONFLICT"
    ARGUMENT_TRANSFORM_FAILED = "ARGUMENT_TRANSFORM_FAILED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    INCOMPLETE_IDENTITY = "INCOMPLETE_IDENTITY"
    MISSING_DECISION = "MISSING_DECISION"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    DECISION_EXPIRED = "DECISION_EXPIRED"
    PROHIBITED_REDIRECT = "PROHIBITED_REDIRECT"


class GovernanceAction(str, Enum):
    """Acción resultante de la evaluación de política.

    HITL exige aprobación humana y sanitización; PASS/BLOCK prohíben la
    aprobación humana como obligación (reglas comunes del plan).
    """

    PASS = "PASS"
    BLOCK = "BLOCK"
    HITL = "HITL"


class Obligation(str, Enum):
    """Obligaciones que una política puede imponer a una tool call.

    R2 — Frontera gobernada: la selección de argumentos exige EXACTAMENTE
    una de estas dos obligaciones. Ninguna o ambas producen POLICY_CONFLICT.
    """

    USE_ORIGINAL_ARGUMENTS = "USE_ORIGINAL_ARGUMENTS"
    USE_TRANSFORMED_ARGUMENTS = "USE_TRANSFORMED_ARGUMENTS"


class HookAborted(Exception):
    """Excepción lanzada por el PEP para abortar una tool call.

    Cuando se lanza esta excepción, la herramienta NO se ejecuta (INV-02).
    """

    def __init__(self, reason: str, source: str):
        self.reason = reason
        self.source = source
        super().__init__(f"[{source}] {reason}")