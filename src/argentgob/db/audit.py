"""
AuditWriter: persiste decisiones y resultados de gobernanza en PostgreSQL.

Fail-open para la observabilidad (NO para el enforcement): si la base de datos
no está disponible, se registra el error pero no se propaga la excepción. El
enforcement no debe fallar por falta de auditoría en el MVP.

INV-05: solo se persisten telemetry_arguments / digest, nunca el payload crudo.
"""
from argentgob.core.config import Settings
from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import ToolCallEnvelope
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class AuditWriter:
    """Escribe registros de auditoría; degrada de forma segura sin DB."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def record_decision(
        self,
        envelope: ToolCallEnvelope,
        decision: PolicyDecision,
        blocked_by: str | None = None,
        latency_pre_ms: float | None = None,
    ) -> None:
        """Inserta una decisión en la tabla governance_decisions."""
        try:
            from argentgob.db.connection import get_connection

            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO governance_decisions (
                            decision_id, event_id, agent_id, agent_role,
                            tool_name, operation_class, action, reason_code,
                            policy_id, payload_digest, blocked_by_guardrail,
                            latency_pre_ms, decided_at
                        ) VALUES (
                            %(decision_id)s, %(event_id)s, %(agent_id)s, %(agent_role)s,
                            %(tool_name)s, %(operation_class)s, %(action)s, %(reason_code)s,
                            %(policy_id)s, %(payload_digest)s, %(blocked_by)s,
                            %(latency_pre_ms)s, %(decided_at)s
                        )
                        ON CONFLICT (decision_id) DO NOTHING
                        """,
                        {
                            "decision_id": decision.decision_id,
                            "event_id": envelope.event_id,
                            "agent_id": envelope.agent.id,
                            "agent_role": envelope.agent.role,
                            "tool_name": envelope.tool_name,
                            "operation_class": envelope.operation_class,
                            "action": decision.action.value,
                            "reason_code": decision.reason_code.value,
                            "policy_id": decision.policy_id,
                            "payload_digest": envelope.payload_digest,
                            "blocked_by": blocked_by,
                            "latency_pre_ms": latency_pre_ms,
                            "decided_at": decision.decided_at,
                        },
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - fail-open de observabilidad
            log.warning(
                "audit_decision_failed",
                detail=str(exc),
                decision_id=decision.decision_id,
            )

    def record_result(
        self,
        envelope: ToolCallEnvelope,
        status: str,
        error: str | None = None,
        latency_post_ms: float | None = None,
        decision_id: str | None = None,
    ) -> None:
        """Inserta/actualiza el resultado de ejecución en governance_events."""
        try:
            from argentgob.db.connection import get_connection

            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO governance_events (
                            event_id, decision_id, tool_name, status,
                            error_message, latency_post_ms, recorded_at
                        ) VALUES (
                            %(event_id)s, %(decision_id)s, %(tool_name)s, %(status)s,
                            %(error_message)s, %(latency_post_ms)s, now()
                        )
                        ON CONFLICT (event_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            error_message = EXCLUDED.error_message,
                            latency_post_ms = EXCLUDED.latency_post_ms,
                            recorded_at = now()
                        """,
                        {
                            "event_id": envelope.event_id,
                            "decision_id": decision_id or "",
                            "tool_name": envelope.tool_name,
                            "status": status,
                            "error_message": (error[:512] if error else None),
                            "latency_post_ms": latency_post_ms,
                        },
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - fail-open de observabilidad
            log.warning(
                "audit_result_failed",
                detail=str(exc),
                event_id=envelope.event_id,
            )
