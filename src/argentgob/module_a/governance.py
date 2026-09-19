"""
GovernanceMiddleware: el PEP (Policy Enforcement Point) del sistema.
Coordina el Módulo B (sanitización) y el Módulo C (evaluación de política +
guardrails), y persiste la evidencia vía AuditWriter.

INV-01/INV-02: el executor de la herramienta nunca corre antes de una decisión
válida, y un BLOCK aborta la tentativa antes del side effect.
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from argentgob.core.config import Settings
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    Obligation,
    ReasonCode,
)
from argentgob.db.audit import AuditWriter
from argentgob.module_b.argument_transforms import (
    ArgumentTransformError,
    select_effective_arguments,
)
from argentgob.module_b.sanitizer import Sanitizer
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.hitl.errors import HitlError
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


def _argument_digest(args: Any) -> str:
    """Digest SHA-256 de los argumentos originales (para la approval H6)."""
    canonical = json.dumps(
        args, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class GovernanceMiddleware:
    """Punto de enforcement síncrono que decide PASS o BLOCK."""

    def __init__(
        self,
        settings: Settings,
        abac: ABACEvaluator,
        guardrails: GuardrailEngine,
        audit: AuditWriter,
        *,
        hold=None,
        approval_service=None,
        notifier=None,
    ):
        self.settings = settings
        self.sanitizer = Sanitizer(settings)
        self.abac = abac
        self.guardrails = guardrails
        self.audit = audit
        # Servicios H6 (aprobación humana durable). Opcionales: si no están
        # inyectados, el flujo HITL degrada al comportamiento R2 (bloquear sin
        # perseguir la aprobación humana). Inyectarlos habilita el protocolo
        # completo (hold + approval durable + notificación).
        self.hold = hold
        self.approval_service = approval_service
        self.notifier = notifier
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

        # 2. Validar l?mites de payload (antes del scanner costoso) ? INV-14.
        if envelope.payload_size_bytes > self.settings.max_payload_bytes:
            self._block(
                envelope,
                ReasonCode.PAYLOAD_LIMIT_EXCEEDED,
                "module_a.pep",
                started,
            )

        # 3. R2 ? Frontera gobernada: verificar integridad del payload.
        #    Un digest incorrecto (tampering) bloquea antes del side effect.
        if not envelope.verify_digest():
            self._block(
                envelope,
                ReasonCode.DIGEST_MISMATCH,
                "module_a.pep",
                started,
            )

        # 4. R2 ? Frontera gobernada: identidad completa.
        #    Una identidad incompleta no autoriza la ejecuci?n (fail-closed).
        if not self._identity_complete(agent):
            self._block(
                envelope,
                ReasonCode.INCOMPLETE_IDENTITY,
                "module_a.pep",
                started,
            )

        # 5. Sanitizar para telemetr?a (M?dulo B).
        envelope.telemetry_arguments = self.sanitizer.sanitize(execution_arguments)

        # 6. Evaluar pol?tica ABAC (M?dulo C).
        decision = self.abac.evaluate(envelope)
        envelope.decision = decision

        # R2 ? Frontera gobernada: decisi?n vencida durante el enforcement.
        #    Una decisi?n vencida no autoriza; se aborta y se exige una nueva
        #    ejecuci?n gobernada (sin reintento autom?tico).
        if decision.is_expired():
            self._block(
                envelope,
                ReasonCode.DECISION_EXPIRED,
                "module_a.pep",
                started,
            )

        if decision.action == GovernanceAction.BLOCK:
            latency_pre_ms = (time.perf_counter() - started) * 1000
            log.warning(
                "policy_block",
                evento="? POLICY BLOCK",
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

        # R2 ? Frontera gobernada: HITL exige aprobaci?n humana.
        #    En R2 la aprobaci?n humana no est? implementada (es H6), por lo
        #    que HITL NO ejecuta la herramienta (cero requests no autorizados).
        if decision.action == GovernanceAction.HITL:
            latency_pre_ms = (time.perf_counter() - started) * 1000
            log.warning(
                "policy_hitl",
                evento="? HITL REQUIRED",
                tool=tool_name,
                reason=decision.reason_code.value,
                decision_id=decision.decision_id,
            )
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(
                seconds=self.settings.approval_ttl_seconds
            )
            idempotency_key = f"hitl:{envelope.event_id}"
            argument_digest = _argument_digest(envelope.execution_arguments)

            if self.approval_service is not None and self.hold is not None:
                try:
                    approval, token_id, token_plan = (
                        self.approval_service.create_pending_and_issue_token(
                            event_id=envelope.event_id,
                            decision_id=decision.decision_id,
                            agent_id=envelope.agent.id,
                            tool_name=envelope.tool_name,
                            argument_digest=argument_digest,
                            idempotency_key=idempotency_key,
                            argued_at=now,
                            expires_at=expires_at,
                            hold_registered=lambda: self._register_hold(
                                envelope, expires_at
                            ),
                        )
                    )
                    if token_id is not None and self.notifier is not None:
                        self.notifier.notify_approval_pending(
                            envelope=envelope,
                            approval_id=approval.approval_id,
                            idempotency_key=idempotency_key,
                            expires_at=expires_at,
                            destination=None,
                            sanitized_preview=envelope.telemetry_arguments,
                        )
                except HitlError as exc:
                    log.warning(
                        "policy_hitl_service_error",
                        evento="? HITL REQUIRED (error de servicio)",
                        tool=tool_name,
                        error=str(exc),
                    )
            self.audit.record_decision(
                envelope, decision, latency_pre_ms=latency_pre_ms
            )
            raise HookAborted(
                reason=f"blocked:{ReasonCode.HUMAN_APPROVAL_REQUIRED.value}",
                source="argentgob.pep",
            )

        # R2 ? Frontera gobernada: selecci?n de argumentos.
        #    Exige exactamente una obligaci?n (USE_ORIGINAL o USE_TRANSFORMED).
        #    Ninguna o ambas producen POLICY_CONFLICT y cero ejecuci?n.
        try:
            envelope.effective_arguments = select_effective_arguments(
                execution_arguments, decision.obligations
            )
        except ArgumentTransformError as exc:
            self._block(
                envelope,
                exc.reason,
                "module_b.argument_transforms",
                started,
            )

# 7. Ejecutar guardrails del dominio (Módulo C).
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

    @staticmethod
    def _identity_complete(agent: AgentIdentity) -> bool:
        """R2 ? Frontera gobernada: identidad completa (fail-closed).

        Una identidad incompleta (sin id, rol o versi?n) no autoriza la
        ejecuci?n. Se exige que todos los campos de identidad est?n presentes.
        """
        return bool(
            agent
            and getattr(agent, "id", None)
            and getattr(agent, "role", None)
            and getattr(agent, "version", None)
        )


    def _register_hold(
        self, envelope: ToolCallEnvelope, expires_at: datetime
    ) -> bool:
        """Registra el hold idempotente de los argumentos (H6).

        Devolverá True si el hold quedó registrado, o False si el servicio de
        hold no está disponible (el token no se emite en ese caso).
        """
        if self.hold is None:
            return False
        try:
            self.hold.hold(
                event_id=envelope.event_id,
                tool_name=envelope.tool_name,
                agent_id=envelope.agent.id,
                argument=envelope.execution_arguments,
                expires_at=expires_at,
            )
            return True
        except Exception:
            log.warning(
                "policy_hitl_hold_error",
                evento="? HITL REQUIRED (hold falló)",
                tool=envelope.tool_name,
                event_id=envelope.event_id,
            )
            return False


def _reason_from_guardrail(guard_reason: str | None) -> ReasonCode:
    """Mapea el motivo textual de un guardrail a un ReasonCode conocido."""
    try:
        return ReasonCode(guard_reason)
    except (ValueError, TypeError):
        # INV-15: fallar de forma segura ante un código desconocido.
        return ReasonCode.INTERNAL_GOVERNANCE_ERROR
