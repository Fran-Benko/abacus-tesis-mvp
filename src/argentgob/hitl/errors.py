"""Excepciones de dominio de H6 (aprobación humana durable y ejecución gobernada).

Centralizan los motivos de fallo del hold, la creación de approvals, la
resolución por auditor, el resume y la ejecución gobernada. Cada excepción
expone un `ReasonCode` estable (ver core/errors.py) para que el PEP pueda
mapearla a una respuesta `blocked:<code>` sin adivinar el motivo.
"""
from argentgob.core.errors import ReasonCode


class HitlError(Exception):
    """Base de errores H6. Lleva un ReasonCode legible por máquina."""

    reason: ReasonCode = ReasonCode.INTERNAL_GOVERNANCE_ERROR

    def __init__(self, message: str, *, reason: ReasonCode | None = None):
        self.reason = reason or self.reason
        super().__init__(message)


class HoldCapacityError(HitlError):
    """El hold en memoria no puede albergar este argumento (por tamaño o tope total)."""

    reason = ReasonCode.HOLD_UNAVAILABLE


class HoldConflictError(HitlError):
    """Misma identidad/evento pero digest distinto: no se reusa el hold."""

    reason = ReasonCode.HOLD_UNAVAILABLE


class ApprovalNotPendingError(HitlError):
    """La aprobación no está PENDING (ya resuelta, vencida o cancelada)."""

    reason = ReasonCode.APPROVAL_NOT_APPROVED


class TargetNotFoundError(HitlError):
    """No existe una aprobación/token que coincida con la evidencia presentada."""

    reason = ReasonCode.TOKEN_INVALID


class TokenConsumError(HitlError):
    """El token no pudo reclamarse (inválido, vencido, reusado o revocado)."""

    reason = ReasonCode.TOKEN_INVALID


class UnauthorizedAuditorError(HitlError):
    """El auditor no está autorizado para resolver esta aprobación."""

    reason = ReasonCode.UNAUTHORIZED_AUDITOR


class NotificationError(HitlError):
    """El notificador no pudo entregar la aprobación pendiente."""

    reason = ReasonCode.NOTIFICATION_FAILED


class ArgumentsUnavailableError(HitlError):
    """Los argumentos originales no están disponibles para re-ejecutar."""

    reason = ReasonCode.EXECUTION_ARGUMENTS_UNAVAILABLE