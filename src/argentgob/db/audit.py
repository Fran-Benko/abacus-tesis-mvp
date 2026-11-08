"""
AuditWriter: persiste decisiones y resultados de gobernanza en PostgreSQL.

Fail-open para la observabilidad (NO para el enforcement): si la base de datos
no está disponible, se registra el error pero no se propaga la excepción. El
enforcement no debe fallar por falta de auditoría en el MVP.

INV-05: solo se persisten telemetry_arguments / digest, nunca el payload crudo.
"""
import json

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
            """Inserta una decisión en governance_decisions y policy_decisions.

            `policy_decisions` es la tabla canónica del round-trip H5: las FKs de
            `approval_requests`, `execution_results` y `audit_chain` apuntan a
            ella (`fk_approval_event_id`, etc.). Insertar la decisión en ambas
            tablas mantiene el modelo consistente cuando el PEP persiste una
            decisión que luego genera una approval (HITL) o una ejecución.
            """
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
                        # Round-trip H5: la misma decisión se persiste en
                        # policy_decisions (tabla referenciada por las FKs de
                        # approval_requests / execution_results / audit_chain).
                        cur.execute(
                            """
                            INSERT INTO policy_decisions (
                                decision_id, event_id, session_id, agent_id,
                                agent_role, tool_name, operation_class, resource,
                                environment, payload_digest, action, risk_level,
                                reason_codes, policy_id, policy_version, policy_digest,
                                matched_policy_ids, effective_priority, conflicts,
                                obligations, transform_spec, decided_at, expires_at,
                                blocked_by_guardrail, latency_pre_ms
                            ) VALUES (
                                %(decision_id)s, %(event_id)s, %(session_id)s, %(agent_id)s,
                                %(agent_role)s, %(tool_name)s, %(operation_class)s, %(resource)s,
                                %(environment)s, %(payload_digest)s, %(action)s, %(risk_level)s,
                                %(reason_codes)s, %(policy_id)s, %(policy_version)s, %(policy_digest)s,
                                %(matched_policy_ids)s, %(effective_priority)s, %(conflicts)s,
                                %(obligations)s, %(transform_spec)s, %(decided_at)s, %(expires_at)s,
                                %(blocked_by)s, %(latency_pre_ms)s
                            )
                            ON CONFLICT (decision_id) DO NOTHING
                            """,
                            {
                                "decision_id": decision.decision_id,
                                "event_id": envelope.event_id,
                                "session_id": getattr(envelope, "session_id", None),
                                "agent_id": envelope.agent.id,
                                "agent_role": envelope.agent.role,
                                "tool_name": envelope.tool_name,
                                "operation_class": envelope.operation_class,
                                "resource": envelope.resource,
                                "environment": envelope.environment,
                                "payload_digest": envelope.payload_digest,
                                "action": decision.action.value,
                                "risk_level": decision.risk_level.value,
                                "reason_codes": json.dumps(
                                    [r.value for r in decision.reason_codes]
                                ),
                                "policy_id": decision.policy_id,
                                "policy_version": decision.policy_version,
                                "policy_digest": decision.policy_digest,
                                "matched_policy_ids": json.dumps(
                                    decision.matched_policy_ids
                                ),
                                "effective_priority": decision.effective_priority,
                                "conflicts": json.dumps(decision.conflicts),
                                "obligations": json.dumps(
                                    [o.value for o in decision.obligations]
                                ),
                                "transform_spec": json.dumps(decision.transform_spec)
                                if decision.transform_spec
                                else None,
                                "decided_at": decision.decided_at,
                                "expires_at": decision.expires_at,
                                "blocked_by": blocked_by,
                                "latency_pre_ms": latency_pre_ms,
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
