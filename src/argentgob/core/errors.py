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
    INTERNAL_GOVERNANCE_ERROR = "INTERNAL_GOVERNANCE_ERROR"


class GovernanceAction(str, Enum):
    """Acción resultante de la evaluación de política. HITL se difiere al MVP+1."""

    PASS = "PASS"
    BLOCK = "BLOCK"


class HookAborted(Exception):
    """Excepción lanzada por el PEP para abortar una tool call.

    Cuando se lanza esta excepción, la herramienta NO se ejecuta (INV-02).
    """

    def __init__(self, reason: str, source: str):
        self.reason = reason
        self.source = source
        super().__init__(f"[{source}] {reason}")
