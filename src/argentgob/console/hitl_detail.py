"""H8 â€” HitlDetailService: detalle sanitizado de una approval (Unidad 1).

Devuelve el detalle de una approval para la consola: IDs, tool/resource,
entorno, riesgo/cÃ³digos, polÃ­tica/versiÃ³n, preview sanitizado (solo digest),
expiraciÃ³n/edad, estado y actor autorizado. NUNCA expone `execution_arguments`
ni el payload crudo (INV-05).

El preview es SOLO el `argument_digest` (hash), nunca el argumento en claro.
"""
from datetime import datetime, timezone
from typing import Any, Callable

from psycopg.rows import dict_row

from argentgob.db.connection import get_connection
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class ApprovalNotFoundError(Exception):
    """La approval solicitada no existe."""


class HitlDetailService:
    """Detalle sanitizado de una approval para la consola."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self._connection_factory = connection_factory or (lambda: get_connection())

    def get_detail(self, *, approval_id: str) -> dict[str, Any]:
        """Devuelve el detalle sanitizado de una approval.

        Lanza `ApprovalNotFoundError` si no existe.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        ar.approval_id,
                        ar.event_id,
                        ar.decision_id,
                        ar.agent_id,
                        ar.tool_name,
                        ar.argument_digest,
                        ar.idempotency_key,
                        ar.state,
                        ar.argued_at,
                        ar.expires_at,
                        ar.resolved_at,
                        ar.resolved_by,
                        ar.decision,
                        d.resource,
                        d.environment,
                        d.risk_level,
                        d.reason_codes,
                        d.policy_id,
                        d.policy_version,
                        d.operation_class
                    FROM approval_requests ar
                    LEFT JOIN policy_decisions d ON d.decision_id = ar.decision_id
                    WHERE ar.approval_id = %(approval_id)s
                    """,
                    {"approval_id": approval_id},
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None:
            raise ApprovalNotFoundError(f"approval {approval_id} no existe")

        detail = self._row_to_dict(row)
        return self._sanitize(detail)

    # â”€â”€ SanitizaciÃ³n â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @staticmethod
    def _sanitize(detail: dict[str, Any]) -> dict[str, Any]:
        """Construye el detalle autorizado (solo campos no sensibles)."""
        now = datetime.now(timezone.utc)
        argued_at = detail.get("argued_at")
        expires_at = detail.get("expires_at")
        resolved_at = detail.get("resolved_at")

        age_seconds = None
        if argued_at is not None:
            age_seconds = max(0, int((now - argued_at).total_seconds()))
        expires_in_seconds = None
        if expires_at is not None:
            expires_in_seconds = max(0, int((expires_at - now).total_seconds()))

        return {
            "approval_id": detail.get("approval_id"),
            "event_id": detail.get("event_id"),
            "decision_id": detail.get("decision_id"),
            "agent_id": detail.get("agent_id"),
            "tool_name": detail.get("tool_name"),
            "operation_class": detail.get("operation_class"),
            "resource": detail.get("resource"),
            "environment": detail.get("environment"),
            "risk_level": detail.get("risk_level"),
            "reason_codes": detail.get("reason_codes"),
            "policy_id": detail.get("policy_id"),
            "policy_version": detail.get("policy_version"),
            # Preview sanitizado: SOLO el digest, nunca el argumento en claro.
            "argument_digest": detail.get("argument_digest"),
            "idempotency_key": detail.get("idempotency_key"),
            "state": detail.get("state"),
            "argued_at": argued_at.isoformat() if argued_at else None,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
            "resolved_by": detail.get("resolved_by"),
            "decision": detail.get("decision"),
            "age_seconds": age_seconds,
            "expires_in_seconds": expires_in_seconds,
        }

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convierte un row psycopg (dict o tuple) a dict."""
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        return {f"col_{i}": v for i, v in enumerate(row)}