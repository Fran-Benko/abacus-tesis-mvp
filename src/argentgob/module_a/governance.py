"""
GovernanceMiddleware: el PEP (Policy Enforcement Point) del sistema.
Coordina el Módulo B (sanitización) y el Módulo C (evaluación de política +
guardrails), y persiste la evidencia vía AuditWriter.

INV-01/INV-02: el executor de la herramienta nunca corre antes de una decisión
válida, y un BLOCK aborta la tentativa antes del side effect.
"""
import time
from typing import Any

from argentgob.core.config import Settings
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import GovernanceAction, HookAborted, ReasonCode
from argentgob.db.audit import AuditWriter
from argentgob.module_b.sanitizer import Sanitizer
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class GovernanceMiddleware:
    """Punto de enforcement síncrono que decide PASS o BLOCK."""

    def __init__(
        self,
        settings: Settings,
        abac: ABACEvaluator,
        guardrails: GuardrailEngine,
        audit: AuditWriter,
    ):
        self.settings = settings
        self.sanitizer = Sanitizer(settings)
        self.abac = abac
        self.guardrails = guardrails
        self.audit = audit
        # Registro temporal de decisiones por evento (para enlazar post_hook).
        self._decision_index: dict[str, str] = {}

    def pre_hook(
        self,
        agent: AgentIdentity,
        tool_name: str,
        operation_class: str,
        resource: str,
        execution_arguments: dict[str, Any],
    ) -> ToolCallEnvelope:
        """Enforcement síncrono. Retorna el envelope si PASS; lanza HookAborted si BLOCK."""
        started = time.perf_counter()

        # 1. Construir el envelope.
        envelope = ToolCallEnvelope.build(
            agent=agent,
            tool_name=tool_name,
            operation_class=operation_class,
            resource=resource,
            environment=self.settings.environment,
            execution_arguments=execution_arguments,
        )

        log.info(
            "pre_hook_start",
            evento="PRE_HOOK",
            tool=tool_name,
            agent_id=agent.id,
            profile=agent.role,
            payload_size=envelope.payload_size_bytes,
            digest=envelope.payload_digest[:20] + "...",
        )

        # 2. Validar límites de payload (antes del scanner costoso) — INV-14.
        if envelope.payload_size_bytes > self.settings.max_payload_bytes:
            self._block(
                envelope,
                ReasonCode.PAYLOAD_LIMIT_EXCEEDED,
                "module_a.pep",
                started,
            )

        # 3. Sanitizar para telemetría (Módulo B).
        envelope.telemetry_arguments = self.sanitizer.sanitize(execution_arguments)

        # 4. Evaluar política ABAC (Módulo C).
        decision = self.abac.evaluate(envelope)

        if decision.action == GovernanceAction.BLOCK:
            latency_pre_ms = (time.perf_counter() - started) * 1000
            log.warning(
                "policy_block",
                evento="⛔ POLICY BLOCK",
                tool=tool_name,
                reason=decision.reason_code.value,
                decision_id=decision.decision_id,
            )
            self.audit.record_decision(
                envelope, decision, latency_pre_ms=latency_pre_ms
            )
            raise HookAborted(
                reason=f"blocked:{decision.reason_code.value}",
                source="argentgob.pep",
            )

        # 5. Ejecutar guardrails del dominio (Módulo C).
        for guard_name, passed, guard_reason in self.guardrails.run_all(envelope):
            if not passed:
                latency_pre_ms = (time.perf_counter() - started) * 1000
                # Reflejar el motivo real del guardrail en la decisión persistida.
                decision.action = GovernanceAction.BLOCK
                decision.reason_code = _reason_from_guardrail(guard_reason)
                log.warning(
                    "guardrail_block",
                    evento="⛔ GUARDRAIL BLOCK",
                    guard=guard_name,
                    tool=tool_name,
                    reason=guard_reason,
                )
                self.audit.record_decision(
                    envelope,
                    decision,
                    blocked_by=guard_name,
                    latency_pre_ms=latency_pre_ms,
                )
                raise HookAborted(
                    reason=f"blocked:{guard_reason}",
                    source=f"argentgob.guardrail.{guard_name}",
                )
            log.info(
                "guardrail_pass",
                evento="GUARDRAIL PASS",
                guard=guard_name,
                tool=tool_name,
            )

        # 6. Registrar la decisión PASS.
        latency_pre_ms = (time.perf_counter() - started) * 1000
        log.info(
            "policy_pass",
            evento="✅ POLICY PASS",
            tool=tool_name,
            decision_id=decision.decision_id,
            reason=decision.reason_code.value,
        )
        self.audit.record_decision(
            envelope, decision, latency_pre_ms=latency_pre_ms
        )
        self._decision_index[envelope.event_id] = decision.decision_id

        return envelope

    def post_hook(
        self,
        envelope: ToolCallEnvelope,
        result: str | None,
        error: Exception | None,
    ) -> None:
        """Registra el resultado/latencia de la ejecución de la herramienta."""
        status = "SUCCESS" if error is None else "FAILED"
        decision_id = self._decision_index.pop(envelope.event_id, None)
        # La latencia de ejecución de la herramienta se mide en H5+; en el MVP
        # el campo queda nullable para no introducir mediciones no confiables.
        latency_post_ms = None
        log.info(
            "post_hook_complete",
            evento="POST_HOOK",
            tool=envelope.tool_name,
            status=status,
            event_id=envelope.event_id,
        )
        self.audit.record_result(
            envelope,
            status=status,
            error=str(error) if error else None,
            latency_post_ms=latency_post_ms,
            decision_id=decision_id,
        )

    def _block(
        self,
        envelope: ToolCallEnvelope,
        reason: ReasonCode,
        source: str,
        started: float,
    ) -> None:
        """Bloqueo directo del PEP (ej. límite de payload) con auditoría."""
        from argentgob.core.decision import PolicyDecision

        latency_pre_ms = (time.perf_counter() - started) * 1000
        decision = PolicyDecision(
            action=GovernanceAction.BLOCK, reason_code=reason
        )
        log.warning(
            "pep_block",
            evento="⛔ PEP BLOCK",
            tool=envelope.tool_name,
            reason=reason.value,
        )
        self.audit.record_decision(
            envelope, decision, latency_pre_ms=latency_pre_ms
        )
        raise HookAborted(reason=f"blocked:{reason.value}", source=source)


def _reason_from_guardrail(guard_reason: str | None) -> ReasonCode:
    """Mapea el motivo textual de un guardrail a un ReasonCode conocido."""
    try:
        return ReasonCode(guard_reason)
    except (ValueError, TypeError):
        # INV-15: fallar de forma segura ante un código desconocido.
        return ReasonCode.INTERNAL_GOVERNANCE_ERROR
