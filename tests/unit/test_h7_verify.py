"""Tests de H7 — Unidad 2: verificación de la cadena (AuditChainVerifier).

Cubren las comprobaciones del verificador con una conexión falsa que soporta
`fetchall` y `fetchone`:

- Cadena vacía => válida.
- Cadena íntegra => válida (secuencia contigua, enlace prev-hash, hash
  recalculado desde el payload persistido, hashes únicos, referencias).
- Secuencia con saltos => sequence_gap.
- Enlace prev-hash roto => prev_hash_mismatch.
- Hash recalculado distinto => hash_mismatch.
- Hash duplicado => duplicate_hash.
- event_id sin referencia => missing_event_reference.
- decision_id sin referencia => missing_decision_reference.
"""
from argentgob.audit.canonical import GENESIS_HASH, canonical_payload, chain_hash
from argentgob.audit.verify import (
    AuditChainVerifier,
    REASON_DUPLICATE_HASH,
    REASON_HASH_MISMATCH,
    REASON_MISSING_DECISION,
    REASON_MISSING_EVENT,
    REASON_PREV_HASH_MISMATCH,
    REASON_SEQUENCE_GAP,
)


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.record.append((sql, params))
        self._rows = self.conn.route(sql, params)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, route):
        self.autocommit = True
        self.route = route
        self.record = []
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True


def _payload(actor="agent-1", decision="PASS", outcome="EXECUTED"):
    return canonical_payload(
        event_type="tool_call",
        entity="stock_price",
        digest="abc",
        outcome=outcome,
        actor=actor,
        decision=decision,
        approval=None,
    )


def _row(seq, prev, chain, event_id="ev-1", decision_id=None, payload=None):
    return {
        "sequence": seq,
        "event_id": event_id,
        "decision_id": decision_id,
        "chain_hash": chain,
        "prev_hash": prev,
        "payload": payload if payload is not None else _payload(),
    }


def _build_chain(n):
    """Construye n filas encadenadas correctamente."""
    rows = []
    prev = GENESIS_HASH
    for i in range(1, n + 1):
        payload = _payload()
        chain = chain_hash(prev, payload)
        rows.append(_row(i, prev, chain, payload=payload))
        prev = chain
    return rows


def _verifier(rows, event_exists=True, decision_exists=True):
    def route(sql, params=None):
        if "FROM audit_chain" in sql:
            return rows
        if "FROM policy_decisions WHERE event_id" in sql:
            return [1] if event_exists else []
        if "FROM policy_decisions WHERE decision_id" in sql:
            return [1] if decision_exists else []
        return []

    return AuditChainVerifier(connection_factory=lambda: _FakeConn(route))


def test_cadena_vacia_es_valida():
    v = _verifier([])
    ok, reason, seq = v.verify()
    assert ok is True
    assert reason is None and seq is None


def test_cadena_integra_es_valida():
    v = _verifier(_build_chain(3))
    ok, reason, seq = v.verify()
    assert ok is True
    assert reason is None and seq is None


def test_secuencia_con_saltos_detecta_gap():
    rows = _build_chain(3)
    # Romper la secuencia: saltar de 2 a 4.
    rows[2]["sequence"] = 4
    v = _verifier(rows)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_SEQUENCE_GAP
    assert seq == 4


def test_enlace_prev_hash_roto_detecta_mismatch():
    rows = _build_chain(3)
    # Alterar el prev_hash de la segunda entrada.
    rows[1]["prev_hash"] = "sha256:" + "f" * 64
    v = _verifier(rows)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_PREV_HASH_MISMATCH
    assert seq == 2


def test_hash_recalculado_distinto_detecta_mismatch():
    rows = _build_chain(3)
    # Alterar el chain_hash almacenado (manipulación del valor).
    rows[1]["chain_hash"] = "sha256:" + "e" * 64
    v = _verifier(rows)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_HASH_MISMATCH
    assert seq == 2


def test_payload_alterado_detecta_mismatch():
    rows = _build_chain(3)
    # Alterar un valor del payload persistido (manipulación del contenido).
    rows[1]["payload"] = rows[1]["payload"].replace('"actor":"agent-1"', '"actor":"x"')
    v = _verifier(rows)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_HASH_MISMATCH
    assert seq == 2


def test_payload_nulo_detecta_mismatch():
    rows = _build_chain(2)
    rows[1]["payload"] = None
    v = _verifier(rows)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_HASH_MISMATCH
    assert seq == 2


def test_hash_duplicado_detecta_duplicate():
    rows = _build_chain(3)
    # Forzar un hash duplicado.
    rows[2]["chain_hash"] = rows[1]["chain_hash"]
    v = _verifier(rows)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_DUPLICATE_HASH
    assert seq == 3


def test_event_id_sin_referencia_detecta_missing_event():
    rows = _build_chain(2)
    v = _verifier(rows, event_exists=False)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_MISSING_EVENT
    assert seq == 1


def test_decision_id_sin_referencia_detecta_missing_decision():
    rows = _build_chain(2)
    rows[1]["decision_id"] = "dec-999"
    v = _verifier(rows, decision_exists=False)
    ok, reason, seq = v.verify()
    assert ok is False
    assert reason == REASON_MISSING_DECISION
    assert seq == 2