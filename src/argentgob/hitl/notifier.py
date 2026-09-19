"""Notifier: notifica la resolucion de una approval pendiente a un humano (Unidad 3).

Unidad 3 exige que el notificador reciba SOLO informacion no sensible:
- approval_id, event_id, idempotency_key, tool_name, destino y expiracion.
- preview sanitizado del argumento (nunca el payload crudo ni el digest?).

Si la notificacion falla, el flujo debe abortar de forma segura con
`NOTIFICATION_FAILED` (no se ejecuta nada sin visibilidad humana).
"""
from typing import Any, Protocol
from datetime import datetime, timezone

from argentgob.core.envelope import ToolCallEnvelope
from argentgob.hitl.errors import NotificationError
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class Notifier(Protocol):
    """Contrato de un canal de notificacion de approvals humanas."""

    def notify(
        self,
        *,
        notification: dict[str, Any],
        destination: str | None = None,
    ) -> bool:
        ...


class ApprovalNotifier:
    """Envia avisos de approvals pendientes por un canal configurable."""

    def __init__(self, channel: Notifier):
        self._channel = channel

    def notify_approval_pending(
        self,
        *,
        envelope: ToolCallEnvelope,
        approval_id: str,
        idempotency_key: str,
        expires_at: datetime | None,
        destination: str | None = None,
        sanitized_preview: dict[str, Any],
    ) -> None:
        """Arma el payload no sensible y lo envia al canal.

        Solo se exponen datos sanitizados: jamás execution_arguments crudos.
        """
        notification = {
            "kind": "approval_pending",
            "approval_id": approval_id,
            "event_id": envelope.event_id,
            "idempotency_key": idempotency_key,
            "tool_name": envelope.tool_name,
            "operation_class": envelope.operation_class,
            "resource": envelope.resource,
            "agent_id": envelope.agent.id if envelope.agent else None,
            "agent_role": envelope.agent.role if envelope.agent else None,
            "destination": destination,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "sanitized_preview": sanitized_preview,
        }
        ok = self._channel.notify(notification=notification, destination=destination)
        if not ok:
            raise NotificationError(
                "fallo la notificacion de la approval pendiente",
            )
        log.info(
            "approval_notified",
            approval_id=approval_id,
            event_id=envelope.event_id,
            destination=destination,
        )


class ConsoleNotifier:
    """Notifier de desarrollo: imprime la approval en el log (redactado)."""

    def notify(
        self,
        *,
        notification: dict[str, Any],
        destination: str | None = None,
    ) -> bool:
        log.info("hitl_notification", notification=notification)
        return True