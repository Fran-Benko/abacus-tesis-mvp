"""Append transaccional a la cadena de auditoría (H7, Unidad 1).

`AuditChainAppender` serializa la lectura de cabeza y el append dentro de la
misma transacción para que writers concurrentes no creen bifurcaciones:

1. Abre una transacción (autocommit=False).
2. Lee la cabeza actual (`SELECT ... ORDER BY sequence DESC LIMIT 1 FOR UPDATE`).
3. Resuelve la secuencia: `head.sequence + 1` si hay cabeza, o `1` si la cadena
   está vacía. La secuencia se asigna transaccionalmente (no se confía en que
   una identidad no tendrá huecos; los huecos por rollback son legítimos y el
   verificador los tolera como "sin saltos" en la secuencia definida).
4. Calcula `chain_hash = sha256(prev_hash + payload)` y hace el INSERT.
5. Commit. Si algo falla, rollback (no queda entidad sin evidencia requerida).

El append y el cambio durable correspondiente son atómicos cuando comparten
PostgreSQL: se ejecutan en la misma transacción. No se mantiene un lock durante
aprobación humana ni durante una llamada externa: el lock de fila (`FOR UPDATE`)
se libera en el commit y no abarca ninguna espera externa.

El timestamp del payload canónico se calcula en Python y se persiste en
`recorded_at` (no se usa `now()` de PostgreSQL) para que el verificador pueda
reconstruir el payload exacto y recalcular el hash.

La fábrica de conexión es inyectable (default `get_connection`) para poder
testear sin PostgreSQL real.
"""
from datetime import datetime, timezone
from typing import Any, Callable

from argentgob.audit.canonical import GENESIS_HASH, canonical_payload, chain_hash
from argentgob.db.connection import get_connection
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class AuditChainAppender:
    """Append transaccional y atómico a `audit_chain`."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self._connection_factory = connection_factory or (lambda: get_connection())

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        entity: str,
        digest: str,
        outcome: str,
        actor: str,
        timestamp=None,
        decision: str | None = None,
        approval: str | None = None,
        action: str | None = None,
        decision_id: str | None = None,
    ) -> int:
        """Añade una entrada a la cadena de forma transaccional y atómica.

        Retorna la `sequence` asignada. Lanza `AuditAppendError` si la
        persistencia de la evidencia previa obligatoria falla (no se ejecuta
        el efecto sin evidencia).
        """
        conn = self._connection_factory()
        previous = conn.autocommit
        try:
            conn.autocommit = False
            try:
                head = self._read_head(conn)
                sequence = (head["sequence"] + 1) if head else 1
                prev_hash = head["chain_hash"] if head else GENESIS_HASH
                ts = timestamp or datetime.now(timezone.utc)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.astimezone(timezone.utc)
                payload = canonical_payload(
                    event_type=event_type,
                    entity=entity,
                    digest=digest,
                    outcome=outcome,
                    actor=actor,
                    timestamp=ts,
                    decision=decision,
                    approval=approval,
                )
                chain = chain_hash(prev_hash, payload)
                self._insert(
                    conn,
                    sequence=sequence,
                    event_id=event_id,
                    decision_id=decision_id,
                    action=action,
                    chain_hash=chain,
                    prev_hash=prev_hash,
                    recorded_at=ts,
                    payload=payload,
                )
                conn.commit()
                return sequence
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = previous
        finally:
            conn.close()

    # ── SQL transaccional ─────────────────────────────────────────────
    def _read_head(self, conn) -> dict[str, Any] | None:
        """Lee la cabeza actual con lock de fila dentro de la transacción."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sequence, chain_hash
                FROM audit_chain
                ORDER BY sequence DESC
                LIMIT 1
                FOR UPDATE
                """
            )
            row = cur.fetchone()
            if row is None:
                return None
            if isinstance(row, dict):
                return {"sequence": row["sequence"], "chain_hash": row["chain_hash"]}
            # Row tipo fila psycopg (tuple): acceder por índice.
            return {"sequence": row[0], "chain_hash": row[1]}

    def _insert(
        self,
        conn,
        *,
        sequence: int,
        event_id: str,
        decision_id: str | None,
        action: str | None,
        chain_hash: str,
        prev_hash: str,
        recorded_at: datetime,
        payload: str,
    ) -> None:
        """Inserta la fila en audit_chain dentro de la transacción."""
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_chain (
                    sequence, event_id, decision_id, action,
                    chain_hash, prev_hash, recorded_at, payload
                ) VALUES (
                    %(sequence)s, %(event_id)s, %(decision_id)s, %(action)s,
                    %(chain_hash)s, %(prev_hash)s, %(recorded_at)s, %(payload)s
                )
                """,
                {
                    "sequence": sequence,
                    "event_id": event_id,
                    "decision_id": decision_id,
                    "action": action,
                    "chain_hash": chain_hash,
                    "prev_hash": prev_hash,
                    "recorded_at": recorded_at,
                    "payload": payload,
                },
            )


class AuditAppendError(Exception):
    """Error al persistir la evidencia previa obligatoria (no se ejecuta)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)