"""ApprovalService: creacion durable e idempotente de approvals PENDING (Unidad 2).

Transaccional sobre `approval_requests` y `approval_resume_tokens`:

1. **Idempotencia + transaccion**: dado un `idempotency_key`, inserta la fila
   PENDING o devuelve la existente (ON CONFLICT/lectura previa).
2. **Orden critico**: el token de resume solo se emite despues de que el hold
   en memoria haya quedado registrado. Si el hold falla tras crear la approval
   (PENDING), la approval se cancela y NO se emite token (no-resumable), para
   no dejar una re-ejecucion sin argumentos disponibles.
3. **Token opaco de un solo uso**: se persiste el hash SHA-256, nunca el plano.

La fabrica de conexion es inyectable (default `get_connection`) para poder
testear sin PostgreSQL real.
"""
from typing import Any, Callable

from argentgob.core.config import Settings
from argentgob.db.connection import get_connection
from argentgob.hitl.errors import ApprovalNotPendingError
from argentgob.hitl.records import ApprovalRequest
from argentgob.hitl.tokens import generate_bearer_token, hash_token
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


def _row_is_pending(row: dict[str, Any]) -> bool:
    return bool(row) and row.get("state") == "PENDING"


class ApprovalService:
    """Registro durable de approvals y emision de tokens de resume."""

    def __init__(
        self,
        settings: Settings,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self.settings = settings
        self._connection_factory = connection_factory or (lambda: get_connection())

    def create_pending_and_issue_token(
        self,
        *,
        event_id: str,
        decision_id: str | None,
        agent_id: str | None,
        tool_name: str,
        argument_digest: str,
        idempotency_key: str,
        argued_at,
        expires_at,
        hold_registered: Callable[[], bool],
    ) -> tuple[ApprovalRequest, str | None, str | None]:
        """Intenta crear una approval PENDING y, si el hold queda registrado,
        emite el token de resume.

        Retorna (approval, token_id, token_plan_clear). Si el hold no se pudo
        registrar, la approval se cancela y ni el token se emite.
        """
        conn = self._connection_factory()
        previous = conn.autocommit
        token_id: str | None = None
        token_plan: str | None = None
        try:
            conn.autocommit = False
            try:
                approval = self._insert_or_get_pending(
                    conn,
                    event_id=event_id,
                    decision_id=decision_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                    argument_digest=argument_digest,
                    idempotency_key=idempotency_key,
                    argued_at=argued_at,
                    expires_at=expires_at,
                )
                # 1. El hold debe registrarse antes de emitir token.
                hold_ok = hold_registered()
                if not hold_ok:
                    self._cancel_non_resumable(conn, approval.approval_id)
                    conn.commit()  # confirma la cancelacion
                    log.warning(
                        "approval_hold_failed",
                        approval_id=approval.approval_id,
                        reason=approval.argument_digest,
                    )
                    return approval, None, None
                # 2. Emision del token de resume (solo se entrega tras commit).
                token_id, token_plan = self._issue_token(
                    conn, approval.approval_id, expires_at
                )
                conn.commit()
                return approval, token_id, token_plan
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = previous
        finally:
            conn.close()

    # ── SQL transaccional ─────────────────────────────────────────────
    def _insert_or_get_pending(
        self,
        conn,
        *,
        event_id: str,
        decision_id: str | None,
        agent_id: str | None,
        tool_name: str,
        argument_digest: str,
        idempotency_key: str,
        argued_at,
        expires_at,
    ) -> ApprovalRequest:
        """Inserta la approval PENDING o recupera la existente por idempotency."""
        approval_id = self._generate_approval_id()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO approval_requests (
                        approval_id, event_id, decision_id, agent_id, tool_name,
                        argument_digest, idempotency_key, state, argued_at,
                        expires_at
                    ) VALUES (
                        %(approval_id)s, %(event_id)s, %(decision_id)s, %(agent_id)s,
                        %(tool_name)s, %(argument_digest)s, %(idempotency_key)s,
                        'PENDING', %(argued_at)s, %(expires_at)s
                    )
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        state = approval_requests.state,
                        resolved_at = approval_requests.resolved_at
                    RETURNING approval_id, event_id, decision_id, agent_id,
                        tool_name, argument_digest, idempotency_key, state,
                        argued_at, expires_at, resolved_at, resolved_by, decision
                    """,
                    {
                        "approval_id": approval_id,
                        "event_id": event_id,
                        "decision_id": decision_id,
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "argument_digest": argument_digest,
                        "idempotency_key": idempotency_key,
                        "argued_at": argued_at,
                        "expires_at": expires_at,
                    },
                )
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - propagar como error de dominio
            raise ApprovalNotPendingError(
                f"no se pudo registrar la approval: {exc}"
            ) from exc
        return self._row_to_approval(row)

    def _issue_token(
        self, conn, approval_id: str, expires_at
    ) -> tuple[str, str]:
        """Crea el registro del token (hash) y retorna (token_id, token_plan).

        El token_plan es el valor plano opaco que se entrega al notificador;
        solo se persiste `hash_token(token_plan)`.
        """
        token_plan = generate_bearer_token()
        token_id = self._generate_token_id()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO approval_resume_tokens (
                    token_id, approval_id, token_hash, state, created_at,
                    expires_at
                ) VALUES (
                    %(token_id)s, %(approval_id)s, %(token_hash)s, 'ACTIVE',
                    now(), %(expires_at)s
                )
                """,
                {
                    "token_id": token_id,
                    "approval_id": approval_id,
                    "token_hash": hash_token(token_plan),
                    "expires_at": expires_at,
                },
            )
        return token_id, token_plan

    def _cancel_non_resumable(self, conn, approval_id: str) -> None:
        """Marca la approval como CANCELLED (no resumable) sin emitir token."""
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE approval_requests SET state = 'CANCELLED'
                WHERE approval_id = %(approval_id)s AND state = 'PENDING'
                """,
                {"approval_id": approval_id},
            )

    # ── Helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _generate_approval_id() -> str:
        import uuid

        return str(uuid.uuid4())

    @staticmethod
    def _generate_token_id() -> str:
        import uuid

        return str(uuid.uuid4())

    @staticmethod
    def _row_to_approval(row) -> ApprovalRequest:
        """Convierte un row psycopg (tuple/dict) a ApprovalRequest."""
        if row is None:
            return ApprovalRequest(
                approval_id="", event_id="", tool_name="", argument_digest="",
                idempotency_key="",
            )
        if isinstance(row, dict):
            return ApprovalRequest(
                approval_id=row["approval_id"],
                event_id=row["event_id"],
                decision_id=row.get("decision_id"),
                agent_id=row.get("agent_id"),
                tool_name=row["tool_name"],
                argument_digest=row["argument_digest"],
                idempotency_key=row["idempotency_key"],
                state=row.get("state") or "PENDING",
                argued_at=row.get("argued_at"),
                expires_at=row.get("expires_at"),
                resolved_at=row.get("resolved_at"),
                resolved_by=row.get("resolved_by"),
                decision=row.get("decision"),
            )
        # Row tipo fila psycopg: acceder por índice/etiqueta.
        return ApprovalRequest(
            approval_id=row[0],
            event_id=row[1],
            decision_id=row[2],
            agent_id=row[3],
            tool_name=row[4],
            argument_digest=row[5],
            idempotency_key=row[6],
            state=row[7],
            argued_at=row[8],
            expires_at=row[9],
            resolved_at=row[10],
            resolved_by=row[11],
            decision=row[12],
        )