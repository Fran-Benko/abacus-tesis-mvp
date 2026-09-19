"""Evaluador ABAC determinista con políticas persistentes (Unidad 4).

Reglas:
- Filtrar por ambiente, identidad/rol, tool, clase y vigencia; evaluar recurso y
  sensibilidad. Tool no registrada bloquea; ausencia de regla bloquea; reglas
  vencidas no autorizan.
- Matching exacto por defecto; solo patrones de prefijo restringido o listas
  exactas documentadas, nunca regex libre.
- Evaluar todas las reglas coincidentes: BLOCK > HITL > PASS.
- Ordenar la explicación por prioridad, especificidad y versión de forma
  determinista; resolver empates sin depender del orden DB.
- Prioridad o especificidad no anulan una prohibición.
- Obligaciones incompatibles o versiones incompatibles para el mismo alcance
  producen `POLICY_CONFLICT`.
- El motor devuelve una decisión; no ejecuta.
"""
from dataclasses import dataclass
from typing import Any

from argentgob.core.config import Settings
from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    Obligation,
    ReasonCode,
    RiskLevel,
)
from argentgob.module_c.policy import GovernancePolicy, select_effective_arguments_h5
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


@dataclass
class _Match:
    """Una política que coincidió con el envelope."""

    policy: GovernancePolicy
    resource_score: int = 0  # especificidad: 1 recurso exacto, 0 comodín
    version_score: int = 0  # versión más alta = mayor prioridad de desempate


# Sensibilidad máxima por nivel de riesgo
_RISK_FROM_OPERATION = {
    "READ": RiskLevel.LOW,
    "EXTERNAL_SEND": RiskLevel.MEDIUM,
    "WRITE": RiskLevel.HIGH,
    "DELETE": RiskLevel.HIGH,
    "DDL": RiskLevel.HIGH,
    "CODE_EXEC": RiskLevel.HIGH,
}


class DeterministicEvaluator:
    """Decide de forma determinista evaluando todas las políticas coincidentes."""

    def __init__(self, settings: Settings, policy_source: Any):
        self.settings = settings
        self.policy_source = policy_source  # PolicyStore con load_current()

    def evaluate(self, envelope: ToolCallEnvelope) -> PolicyDecision:
        policies = self._all_policies()
        matches = self._collect_matches(envelope, policies)

        if not matches:
            return PolicyDecision(
                action=GovernanceAction.BLOCK,
                reason_codes=[ReasonCode.NO_POLICY_MATCHED],
                risk_level=self._risk_for(envelope.operation_class),
                policy_id=None,
                policy_version=None,
                policy_digest=None,
                matched_policy_ids=[],
            )

        return self._resolve(envelope, matches)

    # ── Recopilación ─────────────────────────────────────────────────
    def _all_policies(self) -> list[GovernancePolicy]:
        try:
            return self.policy_source.load_current()
        except Exception:  # noqa: BLE001 - fail-closed ante DB caída
            log.warning("evaluator_policy_source_unavailable")
            return []

    def _collect_matches(
        self, envelope: ToolCallEnvelope, policies: list[GovernancePolicy]
    ) -> list[_Match]:
        matches: list[_Match] = []
        profile = envelope.agent.role if envelope.agent else None
        for p in policies:
            if not p.matches(
                profile_name=profile or "",
                tool_name=envelope.tool_name,
                operation_class=envelope.operation_class,
                environment=envelope.environment,
                resource=envelope.resource,
            ):
                continue
            if not p.is_active():
                continue
            resource_score = 1 if (p.resource and p.resource == envelope.resource) else 0
            version_score = p.policy_version
            matches.append(
                _Match(policy=p, resource_score=resource_score, version_score=version_score)
            )
        return matches

    # ── Resolución ───────────────────────────────────────────────────
    def _resolve(self, envelope: ToolCallEnvelope, matches: list[_Match]) -> PolicyDecision:
        # Precedencia de efectos: BLOCK > HITL > PASS.
        block_matches = [m for m in matches if m.policy.effect == GovernanceAction.BLOCK]
        if block_matches:
            return self._decision_for(
                envelope,
                block_matches,
                GovernanceAction.BLOCK,
                [ReasonCode.INSUFFICIENT_AUTHORIZATION],
            )

        hitl_matches = [m for m in matches if m.policy.effect == GovernanceAction.HITL]
        if hitl_matches:
            return self._decision_for(
                envelope,
                hitl_matches,
                GovernanceAction.HITL,
                [ReasonCode.HUMAN_APPROVAL_REQUIRED],
            )

        pass_matches = [m for m in matches if m.policy.effect == GovernanceAction.PASS]
        if not pass_matches:
            return PolicyDecision(
                action=GovernanceAction.BLOCK,
                reason_codes=[ReasonCode.NO_POLICY_MATCHED],
                risk_level=self._risk_for(envelope.operation_class),
                policy_id=None,
                policy_version=None,
                policy_digest=None,
                matched_policy_ids=[],
            )

        sorted_pass = sorted(
            pass_matches,
            key=lambda m: (
                m.policy.priority,
                m.resource_score,
                m.version_score,
                m.policy.policy_id,
            ),
            reverse=True,
        )
        best = sorted_pass[0]

        # Verificar conflictos de obligaciones entre políticas PASS que coinciden.
        conflicts = self._detect_conflicts(sorted_pass)
        obligations = best.policy.obligations
        transform_spec = best.policy.transform_spec
        if conflicts:
            return PolicyDecision(
                action=GovernanceAction.BLOCK,
                reason_codes=[ReasonCode.POLICY_CONFLICT],
                risk_level=self._risk_for(envelope.operation_class),
                policy_id=best.policy.policy_id,
                policy_version=best.policy.policy_version,
                policy_digest=best.policy.policy_digest,
                matched_policy_ids=[m.policy.policy_id for m in matches],
                effective_priority=best.policy.priority,
                conflicts=conflicts,
                obligations=[],
                transform_spec=transform_spec,
            )

        # Reglas de selección de argumentos: exactamente una obligación.
        try:
            select_effective_arguments_h5(
                envelope.execution_arguments, obligations, transform_spec
            )
        except Exception:  # noqa: BLE001
            return PolicyDecision(
                action=GovernanceAction.BLOCK,
                reason_codes=[ReasonCode.POLICY_CONFLICT],
                risk_level=self._risk_for(envelope.operation_class),
                policy_id=best.policy.policy_id,
                policy_version=best.policy.policy_version,
                policy_digest=best.policy.policy_digest,
                matched_policy_ids=[m.policy.policy_id for m in matches],
                effective_priority=best.policy.priority,
                conflicts=["obligaciones de selección incompatibles"],
                obligations=[],
                transform_spec=transform_spec,
            )

        return self._decision_for(
            envelope,
            sorted_pass,
            GovernanceAction.PASS,
            [ReasonCode.ALLOWED],
        )

    def _decision_for(
        self,
        envelope: ToolCallEnvelope,
        matches: list[_Match],
        action: GovernanceAction,
        reason_codes: list[ReasonCode],
    ) -> PolicyDecision:
        best = max(
            matches,
            key=lambda m: (
                m.policy.priority,
                m.resource_score,
                m.version_score,
                m.policy.policy_id,
            ),
        )
        return PolicyDecision(
            action=action,
            reason_codes=reason_codes,
            risk_level=self._risk_for(envelope.operation_class),
            policy_id=best.policy.policy_id,
            policy_version=best.policy.policy_version,
            policy_digest=best.policy.policy_digest,
            matched_policy_ids=[m.policy.policy_id for m in matches],
            effective_priority=best.policy.priority,
            obligations=best.policy.obligations,
            transform_spec=best.policy.transform_spec,
        )

    @staticmethod
    def _detect_conflicts(matches: list[_Match]) -> list[str]:
        """Detecta obligaciones incompatibles entre políticas coincidentes."""
        conflicts: list[str] = []
        seen: dict[str, set[str]] = {}
        for m in matches:
            for ob in m.policy.obligations:
                seen.setdefault(ob.value, set()).add(m.policy.policy_id)
        # USE_ORIGINAL y USE_TRANSFORMED son mutuamente excluyentes.
        if (
            Obligation.USE_ORIGINAL_ARGUMENTS.value in seen
            and Obligation.USE_TRANSFORMED_ARGUMENTS.value in seen
        ):
            conflicts.append(
                "USE_ORIGINAL/USE_TRANSFORMED incompatibles para el mismo alcance"
            )
        return conflicts

    @staticmethod
    def _risk_for(operation_class: str) -> RiskLevel:
        return _RISK_FROM_OPERATION.get(operation_class, RiskLevel.LOW)