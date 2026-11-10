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
from datetime import UTC, datetime, timedelta
from typing import Any

# Fase 4 — Integración operacional: capa predictiva.
from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.core.config import Settings
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    ReasonCode,
)
from argentgob.core.observer import GovernanceObserver, NullObserver
from argentgob.db.audit import AuditWriter
from argentgob.hitl.errors import HitlError
from argentgob.module_b.argument_transforms import (
    ArgumentTransformError,
    select_effective_arguments,
)
from argentgob.module_b.sanitizer import Sanitizer
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
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
            # Fase 4 — Integración operacional: capa predictiva opcional.
            # Si no se inyectan, el PEP degrada a fallback determinista fail-closed
            # (sin predicción, la decisión la toma el motor determinista).
            feature_builder: FeatureBuilder | None = None,
            risk_predictor: RiskPredictor | None = None,
            prediction_store: PredictionStore | None = None,
            run_id: str | None = None,
            observer: GovernanceObserver | None = None,
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
            # Fase 4 — capa predictiva.
            self.feature_builder = feature_builder
            self.risk_predictor = risk_predictor
            self.prediction_store = prediction_store
            # AV-01 — Correlación de run y observación de fronteras. Sin run ni
            # observador se conserva el comportamiento histórico (CLI/tests).
            self.run_id = run_id
            self.observer: GovernanceObserver = observer or NullObserver()
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
            run_id=self.run_id,
        )
        self.emit_event(
            "tool.requested",
            {
                "event_id": envelope.event_id,
                "tool": tool_name,
                "resource": resource,
                "operation_class": operation_class,
            },
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

        # Fase 4 — Integración operacional: inferencia predictiva.
        #    Se ejecuta ANTES de la decisión determinista; si el modelo está
        #    disponible, puede elevar un PASS del motor a BLOCK (HIGH) o HITL
        #    (MEDIUM). Si no está disponible o falla, la decisión queda en el
        #    motor determinista (fallback fail-closed).
        self._run_predictive_inference(envelope, started)

        # 7. Evaluar pol?tica ABAC (M?dulo C).
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

        # Persistir la predicción con el decision_id final (upsert idempotente).
        #    La predicción se registró antes de la decisión determinista; aquí
        #    se vincula al decision_id una vez que la decisión existe.
        if envelope.prediction is not None and self.prediction_store is not None:
            self.prediction_store.record_prediction(
                envelope, envelope.prediction, decision_id=decision.decision_id
            )

        # Fase 4 — Integración operacional: precedencia de decisión.
        #    1) BLOCK determinista (política/guardrail) gana siempre.
        #    2) Predicción HIGH → BLOCK.
        #    3) Predicción MEDIUM → HITL.
        #    4) Predicción LOW → PASS (mantiene la decisión determinista).
        prediction = envelope.prediction
        if prediction is not None and decision.action != GovernanceAction.BLOCK:
            if prediction.predicted_risk_class == "HIGH":
                decision.action = GovernanceAction.BLOCK
                decision.reason_code = ReasonCode.PREDICTED_HIGH_RISK
            elif (
                prediction.predicted_risk_class == "MEDIUM"
                and decision.action == GovernanceAction.PASS
            ):
                decision.action = GovernanceAction.HITL
                decision.reason_code = ReasonCode.PREDICTED_MEDIUM_RISK

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
            self._emit_decision(envelope, decision, latency_pre_ms)
            raise HookAborted(
                reason=f"blocked:{decision.reason_code.value}",
                source="argentgob.pep",
            )

        # R2 ? Frontera gobernada: HITL exige aprobaci?n humana.
        #    En R2 la aprobaci?n humana no est? implementada (es H6), por lo
        #    que HITL NO ejecuta la herramienta (cero requests no autorizados).
        if decision.action == GovernanceAction.HITL:
            self._elevate_hitl(envelope, decision, started)

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

# 8. Ejecutar guardrails del dominio (Módulo C).
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
                self._emit_decision(
                    envelope, decision, latency_pre_ms, blocked_by=guard_name
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

        # 9. Registrar la decisión PASS.
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
        self._emit_decision(envelope, decision, latency_pre_ms)
        self._decision_index[envelope.event_id] = decision.decision_id

        return envelope

    def _run_predictive_inference(
        self, envelope: ToolCallEnvelope, started: float
    ) -> None:
        """Inferencia predictiva pre-ejecución (Fase 4).

        Construye las features del envelope, predice el riesgo y persiste la
        predicción. NO lanza excepciones: cualquier error degrada a fallback
        (decide el motor determinista).
        """
        if self.risk_predictor is None:
            return
        if not self.risk_predictor.available:
            # INV-11 fail-closed informado: sin modelo, decide el motor
            # determinista. No es un error de enforcement (el fallback es la
            # garantía), pero se registra para trazabilidad operacional.
            log.info(
                "predictive_unavailable_fallback",
                evento="🔮 PREDICCIÓN NO DISPONIBLE (fallback determinista)",
                tool=envelope.tool_name,
            )
            return
        try:
            builder = self.feature_builder or FeatureBuilder(
                policy_source=getattr(self.abac, "policy_source", None),
                rate_limit_consumption=self.guardrails.rate_limit_consumption,
            )
            features = builder.build(envelope)
            prediction = self.risk_predictor.predict(features)
            if prediction is None:
                return
            envelope.prediction = prediction
            if self.prediction_store is not None:
                self.prediction_store.record_prediction(
                    envelope, prediction, decision_id=None
                )
            log.info(
                "predictive_inference",
                evento="🔮 PREDICCIÓN",
                tool=envelope.tool_name,
                predicted=prediction.predicted_risk_class,
                prob_high=round(prediction.risk_probability, 4),
                latency_ms=prediction.inference_latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 - fallback fail-closed
            log.warning(
                "predictive_inference_failed",
                detail=str(exc),
                event_id=envelope.event_id,
            )
            envelope.prediction = None

    def _elevate_hitl(
        self,
        envelope: ToolCallEnvelope,
        decision: Any,
        started: float,
    ) -> None:
        """Eleva la decisión actual a HITL (protocolo H6 completo, si existe).

        Fase 4 — La predicción MEDIUM eleva un PASS del motor a HITL, reutilizando
        el protocolo de aprobación humana durable (hold + approval + notifier).
        """
        latency_pre_ms = (time.perf_counter() - started) * 1000
        log.warning(
            "policy_hitl_predictive",
            evento="? HITL REQUIRED (predicción MEDIUM)",
            tool=envelope.tool_name,
            reason=decision.reason_code.value,
            decision_id=decision.decision_id,
        )
        now = datetime.now(UTC)
        expires_at = now + timedelta(
            seconds=self.settings.approval_ttl_seconds
        )
        idempotency_key = f"hitl:{envelope.event_id}"
        argument_digest = _argument_digest(envelope.execution_arguments)

        # Persistir la decisión (round-trip H5) ANTES de crear la approval:
        # `approval_requests.event_id` tiene FK a `policy_decisions.event_id`, y
        # en PostgreSQL real la fila de la decisión debe existir primero.
        self.audit.record_decision(
            envelope, decision, latency_pre_ms=latency_pre_ms
        )
        self._emit_decision(envelope, decision, latency_pre_ms)
        # Registrar en el índice de decisiones para que un eventual post_hook
        # (camino de excepción no-HookAborted) pueda vincular su resultado con
        # la decisión HITL persistida (misma convención que el camino PASS).
        self._decision_index[envelope.event_id] = decision.decision_id

        if self.approval_service is not None and self.hold is not None:
            try:
                approval, token_id, _token_plan = (
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
                self.emit_event(
                    "approval.pending",
                    {
                        "event_id": envelope.event_id,
                        "decision_id": decision.decision_id,
                        "tool": envelope.tool_name,
                        "approval_ref": approval.approval_id,
                        "state": "WAITING_APPROVAL",
                    },
                )
            except HitlError as exc:
                log.warning(
                    "policy_hitl_service_error",
                    evento="? HITL REQUIRED (error de servicio)",
                    tool=envelope.tool_name,
                    error=str(exc),
                )
        raise HookAborted(
            reason=f"blocked:{ReasonCode.HUMAN_APPROVAL_REQUIRED.value}",
            source="argentgob.pep",
        )

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
        self.emit_event(
            "tool.completed" if error is None else "tool.failed",
            {
                "event_id": envelope.event_id,
                "decision_id": decision_id,
                "tool": envelope.tool_name,
                "execution_status": status,
                "message": None if error is None else type(error).__name__,
            },
        )

    # ── AV-01: observación de fronteras ───────────────────────────────
    def emit_event(self, kind: str, payload: dict[str, Any]) -> None:
        """Publica un evento acotado; una falla de observación no cambia la decisión."""
        try:
            self.observer.on_governance_event(kind, payload)
        except Exception as exc:  # noqa: BLE001 - observación degradada
            log.warning(
                "governance_observer_failed",
                kind=kind,
                error_type=type(exc).__name__,
            )

    def _emit_decision(
        self,
        envelope: ToolCallEnvelope,
        decision: Any,
        latency_pre_ms: float,
        *,
        blocked_by: str | None = None,
    ) -> None:
        """Emite `governance.decided` con la decisión ya persistida."""
        payload: dict[str, Any] = {
            "event_id": envelope.event_id,
            "decision_id": decision.decision_id,
            "tool": envelope.tool_name,
            "resource": envelope.resource,
            "operation_class": envelope.operation_class,
            "action": decision.action.value,
            "reason_codes": [r.value for r in decision.reason_codes],
            "risk_level": decision.risk_level.value,
            "policy_id": decision.policy_id,
            "latency_ms": latency_pre_ms,
        }
        if blocked_by:
            payload["message"] = f"blocked_by:{blocked_by}"
        self.emit_event("governance.decided", payload)

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
        self._emit_decision(envelope, decision, latency_pre_ms)
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
        except Exception:  # noqa: BLE001 - el hold falla cerrado: se loguea y no se emite token
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
