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
    NOT_EXECUTED = "NOT_EXECUTED"
    NEW_GOVERNED_EXECUTION = "NEW_GOVERNED_EXECUTION"
    RESULT_RECOVERED = "RESULT_RECOVERED"
    INCOMPLETE_IDENTITY = "INCOMPLETE_IDENTITY"
    MISSING_DECISION = "MISSING_DECISION"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    DECISION_EXPIRED = "DECISION_EXPIRED"
    PROHIBITED_REDIRECT = "PROHIBITED_REDIRECT"
    # H5 — Policy Engine persistente.
    TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
    NO_POLICY_MATCHED = "NO_POLICY_MATCHED"
    POLICY_EXPIRED = "POLICY_EXPIRED"
    SENSITIVITY_EXCEEDED = "SENSITIVITY_EXCEEDED"
    INSUFFICIENT_AUTHORIZATION = "INSUFFICIENT_AUTHORIZATION"
    INCOMPATIBLE_OBLIGATIONS = "INCOMPATIBLE_OBLIGATIONS"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    # H6 — Aprobación humana durable y ejecución gobernada.
    EXECUTION_ARGUMENTS_UNAVAILABLE = "EXECUTION_ARGUMENTS_UNAVAILABLE"
    TOKEN_INVALID = "TOKEN_INVALID"
    APPROVAL_NOT_APPROVED = "APPROVAL_NOT_APPROVED"
    CAPABILITY_REUSED = "CAPABILITY_REUSED"
    CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
    HOLD_UNAVAILABLE = "HOLD_UNAVAILABLE"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    RESULT_ALREADY_EXISTS = "RESULT_ALREADY_EXISTS"
    UNAUTHORIZED_AUDITOR = "UNAUTHORIZED_AUDITOR"


class GovernanceAction(str, Enum):
    """Acción resultante de la evaluación de política.

    HITL exige aprobación humana y sanitización; PASS/BLOCK prohíben la
    aprobación humana como obligación (reglas comunes del plan).
    """

    PASS = "PASS"
    BLOCK = "BLOCK"
    HITL = "HITL"


class RiskLevel(str, Enum):
    """Nivel de riesgo de una decisión (H5 — round-trip)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


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