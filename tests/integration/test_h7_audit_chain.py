"""Tests de integración de H7 — cadena de auditoría detectable ante manipulación.

Ejercitan el flujo completo append → verify con una conexión falsa compartida
que simula una base de datos en memoria (persiste filas entre llamadas). NO
requieren PostgreSQL.

Cubren:
- Round-trip: appender escribe N entradas encadenadas y el verificador las
  valida como íntegras.
- Detección de manipulación: alterar un payload persistido rompe la cadena y
  el verificador reporta la primera ubicación con motivo sanitizado.
- CLI: exit 0 para cadena íntegra, exit 1 para cadena manipulada.
"""
from datetime import datetime, timezone

from argentgob.audit.append import AuditChainAppender
from argentgob.audit.canonical import GENESIS_HASH, canonical_payload, chain_hash
from argentgob.audit.verify import AuditChainVerifier, REASON_HASH_MISMATCH


class _MemCursor:
    """Cursor que delega en la conexión en memoria."""

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

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MemConn:
    """Conexión falsa que persiste filas de audit_chain en memoria."""

    def __init__(self, store):
        self.autocommit = True
        self.store = store
        self.record = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _MemCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def route(self, sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            # Cabeza de la cadena.
            if not self.store["rows"]:
                return []
            head = max(self.store["rows"], key=lambda r: r["sequence"])
            return [head]
        if "INSERT INTO audit_chain" in sql:
            self.store["rows"].append(dict(params))
            return [1]
        if "FROM audit_chain" in sql:
            return sorted(self.store["rows"], key=lambda r: r["sequence"])
        if "FROM policy_decisions WHERE event_id" in sql:
            return [1]
        if "FROM policy_decisions WHERE decision_id" in sql:
            return [1]
        return []


def _make_store():
    return {"rows": []}


def _appender(store):
    return AuditChainAppender(connection_factory=lambda: _MemConn(store))


def _verifier(store):
    return AuditChainVerifier(connection_factory=lambda: _MemConn(store))


def _append_entry(appender, store, event_id, actor="agent-1", decision_id=None):
    return appender.append(
        event_id=event_id,
        event_type="tool_call",
        entity="stock_price",
        digest=f"digest-{event_id}",
        outcome="EXECUTED",
        actor=actor,
        decision_id=decision_id,
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_round_trip_append_verify_cadena_integra():
    store = _make_store()
    appender = _appender(store)
    verifier = _verifier(store)

    seq1 = _append_entry(appender, store, "ev-1")
    seq2 = _append_entry(appender, store, "ev-2")
    seq3 = _append_entry(appender, store, "ev-3")

    assert (seq1, seq2, seq3) == (1, 2, 3)

    ok, reason, seq = verifier.verify()
    assert ok is True
    assert reason is None and seq is None


def test_manipulacion_de_payload_rompe_cadena():
    store = _make_store()
    appender = _appender(store)
    verifier = _verifier(store)

    _append_entry(appender, store, "ev-1")
    _append_entry(appender, store, "ev-2")

    # Manipular el payload persistido de la segunda entrada.
    row2 = next(r for r in store["rows"] if r["sequence"] == 2)
    row2["payload"] = row2["payload"].replace('"actor":"agent-1"', '"actor":"x"')

    ok, reason, seq = verifier.verify()
    assert ok is False
    assert reason == REASON_HASH_MISMATCH
    assert seq == 2


def test_manipulacion_de_chain_hash_rompe_cadena():
    store = _make_store()
    appender = _appender(store)
    verifier = _verifier(store)

    _append_entry(appender, store, "ev-1")
    _append_entry(appender, store, "ev-2")

    # Alterar el chain_hash almacenado.
    row2 = next(r for r in store["rows"] if r["sequence"] == 2)
    row2["chain_hash"] = "sha256:" + "e" * 64

    ok, reason, seq = verifier.verify()
    assert ok is False
    assert reason == REASON_HASH_MISMATCH
    assert seq == 2


def test_cli_exit_0_cadena_integra(monkeypatch, capsys):
    store = _make_store()
    appender = _appender(store)
    _append_entry(appender, store, "ev-1")

    from argentgob.audit import cli

    monkeypatch.setattr(
        cli.AuditChainVerifier,
        "verify",
        lambda self: (True, None, None),
    )
    code = cli.main([])
    out = capsys.readouterr().out
    assert code == 0
    assert "íntegra" in out


def test_cli_exit_1_cadena_manipulada(monkeypatch, capsys):
    from argentgob.audit import cli

    monkeypatch.setattr(
        cli.AuditChainVerifier,
        "verify",
        lambda self: (False, REASON_HASH_MISMATCH, 2),
    )
    code = cli.main([])
    err = capsys.readouterr().err
    assert code == 1
    assert "sequence=2" in err
    assert "manipulación" in err