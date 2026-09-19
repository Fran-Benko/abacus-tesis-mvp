"""Resolver: resolucion de una approval pendiente por un auditor autorizado (Unidad 3).

Normas:
- El auditor debe estar autorizado (identidad con rol en `authorized_auditor_roles`).
- La resolucion es transaccional y condicional: solo una fila pasa de PENDING a
  APPROVED/REJECTED si sigue PENDING, el digest coincide y no vencio.
- Si la fila no se actualiza (0 rows), se lee el estado para distinguir:
  vencida, digest incorrecto o ya resuelta. No se puede resolver un estado no
  PENDING (idempotencia: segundo intento de resolver no rompe nada).
- Se registra `resolved_by`, `resolved_at`, `decision` y el digest presentado.
"""
from typing import Any, Callable

from argentgob.core.envelope import AgentIdentity
from argentgob.db.connection import get_connection
from argentgob.hitl.errors import (
    ApprovalNotPendingError,
    TargetNotFoundError,
    UnauthorizedAuditorError,
)
from argentgob.hitl.tokens import hash_token
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class ApprovalResolver:
    """Resuelve approvals PENDING de forma transaccional y condicional."""

    def __init__(
        self,
        settings,
        auditor_roles: list[str] | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self.settings = settings
        # Roles que pueden resolver; default: lo configurado en Settings.
        self.auditor_roles = (
            list(settings.authorized_auditor_roles)
            if auditor_roles is None
            else list(auditor_roles)
        )
        self._connection_factory = connection_factory or (lambda: get_connection())

    def resolve(
        self,
        *,
        approval: dict[str, Any],
        auditor: AgentIdentity,
        decision: str,  # APPROVED | REJECTED
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        """Resuelve la approval.

        Retorna True si la resolucion se aplico (cambio de estado en la DB);
        False si no habia nada que resolver (ya resuelta / vencida / no en el
        estado esperado). Lanza errores ante auditor no autorizado o fallo.
        """
        if approval is None:
            raise TargetNotFoundError("no hay approval para resolver")
        if auditor is None or auditor.role not in self.auditor_roles:
            raise UnauthorizedAuditorError(
                f"el rol '{getattr(auditor, 'role', None)}' no esta autorizado"
                " para resolver approvals",
            )
        decision_norm = decision.upper()
        if decision_norm not in ("APPROVED", "REJECTED"):
            approval_id = approval.get("approval_id")
            raise ApprovalNotPendingError(
                f"decision invalida: {decision!r} ({approval_id})"
            )

        conn = self._connection_factory()
        previous = conn.autocommit
        try:
            conn.autocommit = False
            try:
                updated = self._apply_resolution(
                    conn,
                    approval,
                    auditor,
                    decision_norm,
                )
                conn.commit()
                return updated
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = previous
        finally:
            conn.close()

    # ── Interno ───────────────────────────────────────────────────────
    def _apply_resolution(self, conn, approval, auditor, decision_norm) -> bool:
        """UPDATE condicional: solo PENDING, digest correcto y no vencida."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        credibility = approval.get("argument_digest")
        approval_id = approval.get("approval_id")
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE approval_requests
                SET state = %(decision)s,
                    resolved_at = %(resolved_at)s,
                    resolved_by = %(resolved_by)s,
                    decision = %(decision)s
                WHERE approval_id = %(approval_id)s
                  AND state = 'PENDING'
                  AND argument_digest = %(digest)s
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                {
                    "decision": decision_norm,
                    "resolved_at": now,
                    "resolved_by": auditor.id,
                    "approval_id": approval_id,
                    "digest": credibility,
                },
            )
            if cur.rowcount == 1:
                log.info(
                    "approval_resolved",
                    approval_id=approval_id,
                    decision=decision_norm,
                    by=auditor.id,
                )
                return True
            if cur.rowcount > 1:  # no deberia pasar con PK approval_id
                log.warning("approval_resolution_ambiguous", approval_id=approval_id)
                return False
        # 0 rows: leer el estado para diagnosticar (vencida/digest/ya resuelta).
        self._diagnose_conflict(conn, approval, decision_norm)
        return False

    def _diagnose_conflict(
        self, conn, approval: dict[str, Any], decision_norm: str
    ) -> None:
        """Lee el estado real para distinguir vencida / digest mal / ya resuelta."""
        from datetime import datetime, timezone

        approval_id = approval.get("approval_id")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state, argument_digest, resolved_at, expires_at
                FROM approval_requests WHERE approval_id = %(approval_id)s
                """,
                {"approval_id": approval_id},
            )
            row = cur.fetchone()
        if row is None:
            raise TargetNotFoundError(f"approval {approval_id} no existe")
        state, digest, resolved_at, expires_at = row
        if state != "PENDING":
            raise ApprovalNotPendingError(
                f"approval {approval_id} ya resuelta: state={state}"
            )
        if digest != approval.get("argument_digest"):
            raise ApprovalNotPendingError(
                f"approval {approval_id}: digest no coincide (evidence tampering)"
            )
        # Solo queda: vencida (expires_at <= now). Avisamos, no resolvemos.
        log.warning(
            "approval_resolution_conflict",
            approval_id=approval_id,
            reason="expired_or_state_conflict",
        )
        raise ApprovalNotPendingError(
            f"approval {approval_id} no resoluble: vencida o conflicto de estado"
        )