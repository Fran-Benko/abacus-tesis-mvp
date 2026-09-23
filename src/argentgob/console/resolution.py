"""H8 — ResolutionService: resolución autorizada de approvals (Unidad 3).

Cierra el puente entre la cadena de auditoría standalone (H7) y el runtime de
gobernanza (H5/H6): aprobar/rechazar una approval exige identidad autorizada
(rol auditor) + evidencia de autenticación vigente en el backend, no solo
ocultar el botón en la UI.

Normas:
- `resolve` exige `evidence` vigente y rol `auditor` (AppRole.AUDITOR) antes de
  delegar en `ApprovalResolver`.
- Distingue el resultado de la resolución (APPROVED/REJECTED aplicado) del
  estado de ejecución posterior (SUCCESS/FAILED/UNKNOWN): aprobar NO garantiza
  que la ejecución gobernada haya tenido éxito.
- Maneja la carrera: dos sesiones resuelven la misma approval; solo una gana
  (la otra recibe `ApprovalNotPendingError`/`already_resolved`).
- Hold perdido: si la approval ya no está PENDING (vencida o resuelta), se
  reporta como `blocked` sin falso éxito.
"""
from typing import Any, Callable

from argentgob.console.hitl_detail import ApprovalNotFoundError, HitlDetailService
from argentgob.identity.evidence import AuthenticationEvidence
from argentgob.identity.provider import (
    AuditorIdentityProvider,
    ExpiredEvidenceError,
    UnauthenticatedError,
    UnauthorizedRoleError,
)
from argentgob.identity.roles import AppRole
from argentgob.hitl.errors import (
    ApprovalNotPendingError,
    TargetNotFoundError,
    UnauthorizedAuditorError,
)
from argentgob.hitl.resolver import ApprovalResolver
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class ResolutionError(Exception):
    """Error de resolución (identidad, evidencia o estado no resoluble)."""


class ResolutionService:
    """Resuelve approvals con identidad autorizada + evidencia vigente."""

    def __init__(
        self,
        *,
        identity_provider: AuditorIdentityProvider,
        resolver: ApprovalResolver,
        detail_service: HitlDetailService | None = None,
    ):
        self._identity = identity_provider
        self._resolver = resolver
        self._detail = detail_service or HitlDetailService()

    def resolve(
        self,
        *,
        approval_id: str,
        decision: str,  # APPROVED | REJECTED
        evidence: AuthenticationEvidence | None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Resuelve una approval con autorización y evidencia vigente.

        Retorna un dict con `outcome`:
        - `"approved"` / `"rejected"`: la resolución se aplicó.
        - `"already_resolved"`: la approval ya no estaba PENDING (carrera).
        - `"blocked"`: no resoluble (vencida / no existe / no autorizado).

        `comment` es un comentario sanitizado opcional (nunca argumentos).
        """
        # 1. Autorización en el backend: evidencia vigente + rol auditor.
        try:
            self._identity.require_role(evidence, AppRole.AUDITOR)
        except (UnauthenticatedError, ExpiredEvidenceError, UnauthorizedRoleError) as exc:
            log.warning("resolution_denied", reason=str(exc))
            return {
                "outcome": "blocked",
                "reason": "unauthorized",
                "approval_id": approval_id,
            }

        # 2. Cargar el detalle (para el digest y para distinguir estados).
        try:
            detail = self._detail.get_detail(approval_id=approval_id)
        except ApprovalNotFoundError:
            return {
                "outcome": "blocked",
                "reason": "not_found",
                "approval_id": approval_id,
            }

        if detail.get("state") != "PENDING":
            # Hold perdido / ya resuelta: sin falso éxito.
            return {
                "outcome": "already_resolved",
                "reason": "not_pending",
                "approval_id": approval_id,
                "state": detail.get("state"),
            }

        # 3. Delegar en el resolver transaccional (condicional, maneja carrera).
        approval = {
            "approval_id": approval_id,
            "argument_digest": detail.get("argument_digest"),
        }
        try:
            applied = self._resolver.resolve(
                approval=approval,
                auditor=self._to_agent_identity(evidence),
                decision=decision,
                evidence=evidence.to_dict() if evidence else None,
            )
        except (ApprovalNotPendingError, TargetNotFoundError) as exc:
            # Carrera: otra sesión resolvió primero, o venció entre lectura y
            # resolución. No es un falso éxito.
            log.warning("resolution_race", approval_id=approval_id, reason=str(exc))
            return {
                "outcome": "already_resolved",
                "reason": "race_or_expired",
                "approval_id": approval_id,
            }
        except UnauthorizedAuditorError as exc:
            log.warning("resolution_denied", approval_id=approval_id, reason=str(exc))
            return {
                "outcome": "blocked",
                "reason": "unauthorized",
                "approval_id": approval_id,
            }

        if not applied:
            return {
                "outcome": "already_resolved",
                "reason": "race_or_expired",
                "approval_id": approval_id,
            }

        outcome = "approved" if decision.upper() == "APPROVED" else "rejected"
        log.info(
            "resolution_applied",
            approval_id=approval_id,
            decision=decision.upper(),
            by=evidence.identifier if evidence else None,
            comment=bool(comment),
        )
        return {
            "outcome": outcome,
            "reason": "applied",
            "approval_id": approval_id,
            "decision": decision.upper(),
            "resolved_by": evidence.identifier if evidence else None,
        }

    # ── Adaptador de identidad ───────────────────────────────────────
    @staticmethod
    def _to_agent_identity(evidence: AuthenticationEvidence | None):
        """Convierte la evidencia en un AgentIdentity para el resolver."""
        from argentgob.core.envelope import AgentIdentity

        if evidence is None:
            return AgentIdentity(id="unknown", role="")
        return AgentIdentity(id=evidence.identifier, role=AppRole.AUDITOR.value)