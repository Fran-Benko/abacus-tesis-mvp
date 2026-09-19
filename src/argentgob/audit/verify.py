"""Verificación de la cadena de auditoría desde el génesis (H7, Unidad 2).

El verificador recorre `audit_chain` en orden de secuencia y comprueba:

1. **Secuencia sin saltos**: la secuencia es estrictamente creciente y cada
   entrada es `anterior + 1`. Una secuencia no contigua indica manipulación.
2. **Enlace prev-hash**: `prev_hash` de cada entrada coincide con el
   `chain_hash` de la anterior (la primera encadena contra `GENESIS_HASH`).
3. **Hash recalculado**: `chain_hash` coincide con el SHA-256 recalculado del
   payload canónico persistido (`payload`). Un cambio de valor en cualquier
   campo canónico produce un hash distinto (detección de manipulación).
4. **Hashes únicos**: no hay `chain_hash` duplicados.
5. **Referencias de entidad**: `event_id` referenciado existe en
   `policy_decisions` (FK), y `decision_id` referenciado existe.

El verificador NO repara ni recalcula silenciosamente la historia alterada:
reporta la primera ubicación (sequence) y un motivo sanitizado. Solo retorna
válido (exit 0) si TODA la cadena es consistente.
"""
from typing import Any, Callable

from argentgob.audit.canonical import GENESIS_HASH, recompute_chain_hash
from argentgob.db.connection import get_connection

# Motivos sanitizados (sin datos crudos, INV-05).
REASON_SEQUENCE_GAP = "sequence_gap"
REASON_PREV_HASH_MISMATCH = "prev_hash_mismatch"
REASON_HASH_MISMATCH = "hash_mismatch"
REASON_DUPLICATE_HASH = "duplicate_hash"
REASON_MISSING_EVENT = "missing_event_reference"
REASON_MISSING_DECISION = "missing_decision_reference"


class AuditVerificationError(Exception):
    """Error de infraestructura al verificar (no un hallazgo de la cadena)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class AuditChainVerifier:
    """Verifica la integridad de la cadena de auditoría desde el génesis."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self._connection_factory = connection_factory or (lambda: get_connection())

    def verify(self) -> tuple[bool, str | None, int | None]:
        """Verifica la cadena completa.

        Retorna `(ok, reason, sequence)`:
        - `ok=True` si la cadena es íntegra (reason/sequence None).
        - `ok=False` con el primer motivo sanitizado y la `sequence` de la
          primera ubicación problemática.
        Lanza `AuditVerificationError` ante fallos de infraestructura.
        """
        conn = self._connection_factory()
        try:
            rows = self._load_chain(conn)
            if not rows:
                # Cadena vacía: válida por definición (no hay historia que
                # verificar).
                return True, None, None
            return self._check(rows)
        finally:
            conn.close()

    # ── Carga ────────────────────────────────────────────────────────
    def _load_chain(self, conn) -> list[dict[str, Any]]:
        """Carga la cadena ordenada por secuencia, con el payload persistido."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ac.sequence,
                    ac.event_id,
                    ac.decision_id,
                    ac.chain_hash,
                    ac.prev_hash,
                    ac.payload
                FROM audit_chain ac
                ORDER BY ac.sequence ASC
                """
            )
            rows = cur.fetchall()
            return [
                self._row_to_chain(r)
                for r in rows
            ]

    def _row_to_chain(self, row) -> dict[str, Any]:
        """Convierte un row psycopg (tuple/dict) a dict de cadena."""
        if isinstance(row, dict):
            return {
                "sequence": row["sequence"],
                "event_id": row["event_id"],
                "decision_id": row["decision_id"],
                "chain_hash": row["chain_hash"],
                "prev_hash": row["prev_hash"],
                "payload": row["payload"],
            }
        # Row tipo fila psycopg (tuple): acceder por índice.
        return {
            "sequence": row[0],
            "event_id": row[1],
            "decision_id": row[2],
            "chain_hash": row[3],
            "prev_hash": row[4],
            "payload": row[5],
        }

    # ── Comprobaciones ───────────────────────────────────────────────
    def _check(self, rows: list[dict[str, Any]]) -> tuple[bool, str | None, int | None]:
        seen_hashes: set[str] = set()
        prev_chain_hash: str | None = None
        prev_sequence: int | None = None

        for row in rows:
            seq = row["sequence"]
            chain = row["chain_hash"]
            prev = row["prev_hash"]

            # 1. Secuencia sin saltos.
            if prev_sequence is not None and seq != prev_sequence + 1:
                return False, REASON_SEQUENCE_GAP, seq

            # 2. Enlace prev-hash.
            expected_prev = prev_chain_hash if prev_chain_hash is not None else GENESIS_HASH
            if prev != expected_prev:
                return False, REASON_PREV_HASH_MISMATCH, seq

            # 3. Hashes únicos. Un hash duplicado es siempre inválido (el
            #    encadenamiento hace imposible un duplicado legítimo), así que
            #    se detecta antes del recálculo.
            if chain in seen_hashes:
                return False, REASON_DUPLICATE_HASH, seq
            seen_hashes.add(chain)

            # 4. Hash recalculado desde el payload canónico persistido.
            if row["payload"] is None:
                return False, REASON_HASH_MISMATCH, seq
            recomputed = recompute_chain_hash(prev, row["payload"])
            if chain != recomputed:
                return False, REASON_HASH_MISMATCH, seq

            # 5. Referencias de entidad.
            if not self._event_exists(row["event_id"]):
                return False, REASON_MISSING_EVENT, seq
            if row["decision_id"] is not None:
                if not self._decision_exists(row["decision_id"]):
                    return False, REASON_MISSING_DECISION, seq

            prev_chain_hash = chain
            prev_sequence = seq

        return True, None, None

    def _event_exists(self, event_id: str) -> bool:
        """Comprueba que el evento referenciado existe en policy_decisions."""
        conn = self._connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM policy_decisions WHERE event_id = %(e)s",
                    {"e": event_id},
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    def _decision_exists(self, decision_id: str) -> bool:
        """Comprueba que la decisión referenciada existe."""
        conn = self._connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM policy_decisions WHERE decision_id = %(d)s",
                    {"d": decision_id},
                )
                return cur.fetchone() is not None
        finally:
            conn.close()