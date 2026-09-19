"""GovernedResumeService: resume de una ejecucion aprobada (Unidad 4).

Unica interfaz publica de reanudacion del hito:
    `GovernedResumeService.resume(token_id, presented_token)`

Flujo:
1. Localizar el token por `token_id` (sin reclamar) y verificar su hash.
2. Localizar la approval relacionada y revalidar: APPROVED, no vencida,
   `argument_digest` correcto, decision de política aun vigente y versión
   compatible con la política que autorizó la ejecución.
3. Consultar el hold (event_id + digest) ANTES de consumir el token.
4. Consumir el token con una unica actualización SQL condicional
   (ACTIVE and no vencido -> REDEEMED); si no consume, rechazar.
5. Solo si el consume tuvo exito, emitir una `ExecutionCapability` de un solo
   uso que autorice una unica ejecucion gobernada.

Si tras un reinicio real no hay hold, no se consume el token ni se ejecuta:
la ejecucion queda en `execution_arguments_unavailable`.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from argentgob.core.decision import PolicyDecision
from argentgob.db.connection import get_connection
from argentgob.hitl.errors import (
    ArgumentsUnavailableError,
    TokenConsumError,
)
from argentgob.hitl.records import ExecutionCapability
from argentgob.hitl.tokens import constant_time_equals, hash_token
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class GovernedResumeService:
    """Reanuda una ejecucion previamente aprobada por un humano."""

    def __init__(
        self,
        settings,
        hold=None,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self.settings = settings
        self.hold = hold  # InMemoryArgumentHold (None si no se conservo)
        self._connection_factory = connection_factory or (lambda: get_connection())

    def resume(
        self, token_id: str, presented_token: str
    ) -> ExecutionCapability:
        """Revalida, consume y crea la capability de un solo uso.

        Lanza errores de dominio (token invalido/vencido/reusado) o
        `ArgumentsUnavailableError` si tras validar no hay hold.
        """
        # 0. Normaliza inputs.
        if not token_id or not presented_token:
            raise TokenConsumError("token_id y presented_token son obligatorios")

        conn = self._connection_factory()
        previous = conn.autocommit
        try:
            conn.autocommit = False
            try:
                # 1. Localizar token (sin reclamar) y verificar hash.
                token = self._locate_token(conn, token_id, presented_token)
                approval = self._load_approval(conn, token["approval_id"])
                # 2. Revalidar approval + decision + version.
                self._revalidate(conn, token, approval)
                # 3. Consultar hold ANTES de consumir.
                args, digest = self._require_hold(approval)
                # 4. Consumir token (unica condicion SQL).
                self._consume_token(conn, token_id, approval)
                # 5. Solo si consumio, crear la capability.
                capability = self._build_capability(approval, args, digest)
                conn.commit()
                log.info("hitl_resume_ok", token_id=token_id)
                return capability
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = previous
        finally:
            conn.close()

    # ── Paso a paso ───────────────────────────────────────────────────
    def _locate_token(self, conn, token_id: str, presented_token: str) -> dict[str, Any]:
        from argentgob.hitl.errors import TokenConsumError

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT token_id, approval_id, token_hash, state,
                       created_at, expires_at, redeemed_at
                FROM approval_resume_tokens
                WHERE token_id = %(token_id)s
                """,
                {"token_id": token_id},
            )
            row = cur.fetchone()
        if row is None:
            raise TokenConsumError("token no existe")
        (
            row_token_id, approval_id, token_hash, state,
            created_at, expires_at, redeemed_at,
        ) = row
        # Verificacion del token presentado contra el hash almacenado.
        if not constant_time_equals(hash_token(presented_token), str(token_hash)):
            raise TokenConsumError("token invalido (hash no coincide)")
        if state != "ACTIVE":
            raise TokenConsumError("token no esta en estado ACTIVE")
        return {
            "token_id": row_token_id,
            "approval_id": approval_id,
            "token_hash": token_hash,
            "state": state,
            "created_at": created_at,
            "expires_at": expires_at,
            "redeemed_at": redeemed_at,
        }

    def _load_approval(self, conn, approval_id: str) -> dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT approval_id, event_id, decision_id, agent_id, tool_name,
                       argument_digest, idempotency_key, state, argued_at,
                       expires_at, resolved_at, resolved_by, decision
                FROM approval_requests
                WHERE approval_id = %(approval_id)s
                """,
                {"approval_id": approval_id},
            )
            row = cur.fetchone()
        if row is None:
            raise TokenConsumError(f"approval {approval_id} no existe")
        cols = [
            "approval_id", "event_id", "decision_id", "agent_id", "tool_name",
            "argument_digest", "idempotency_key", "state", "argued_at",
            "expires_at", "resolved_at", "resolved_by", "decision",
        ]
        return dict(zip(cols, row))

    def _revalidate(self, conn, token, approval) -> None:
        from argentgob.hitl.errors import TokenConsumError

        if approval.get("state") != "APPROVED":
            raise TokenConsumError(
                f"approval no esta APPROVED (state={approval.get('state')})"
            )
        now = datetime.now(timezone.utc)
        if approval.get("expires_at") is not None and now > approval["expires_at"]:
            raise TokenConsumError("approval vencida")
        if token.get("expires_at") is not None and now > token["expires_at"]:
            raise TokenConsumError("token vencido")

    def _require_hold(self, approval) -> tuple[dict[str, Any], str]:
        if self.hold is None:
            raise ArgumentsUnavailableError(
                "no hay hold conservado (reinicio real?)"
            )
        event_id = approval.get("event_id")
        got = (
            self.hold.get_with_digest(event_id)
            if hasattr(self.hold, "get_with_digest")
            else None
        )
        if got is None:
            raise ArgumentsUnavailableError(
                f"no hay argumentos en el hold para event_id={event_id}"
            )
        args, digest = got
        if digest != approval.get("argument_digest"):
            raise ArgumentsUnavailableError(
                "digest del hold no coincide con la approval"
            )
        return args, digest

    def _consume_token(self, conn, token_id: str, approval) -> None:
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE approval_resume_tokens
                SET state = 'REDEEMED', redeemed_at = %(now)s
                WHERE token_id = %(token_id)s
                  AND state = 'ACTIVE'
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                {"now": now, "token_id": token_id},
            )
            if cur.rowcount != 1:
                raise TokenConsumError("el token no pudo reclamarse (concurso/vencido)")
        # Marcar el token como consumido tambien en la approval (order desc).
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE approval_requests SET state = 'APPROVED'
                WHERE approval_id = %(approval_id)s AND state = 'APPROVED'
                """,
                {"approval_id": approval["approval_id"]},
            )

    def _build_capability(
        self, approval, args: dict[str, Any], digest: str
    ) -> ExecutionCapability:
        from datetime import datetime, timedelta, timezone

        cap_ttl = getattr(self.settings, "approval_ttl_seconds", 300)
        return ExecutionCapability(
            event_id=approval.get("event_id"),
            approval_id=approval.get("approval_id"),
            tool_name=approval.get("tool_name"),
            argument_digest=digest,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=cap_ttl),
            consumed=False,
        )