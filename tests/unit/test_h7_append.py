"""Tests de H7 — Unidad 1: append transaccional (AuditChainAppender).

Cubren el flujo transaccional `append` con una conexión falsa (route
inyectable):

- Primera entrada: secuencia 1, prev_hash = GENESIS_HASH.
- Entradas siguientes: secuencia = head+1, prev_hash = head.chain_hash.
- El append y el cambio durable comparten la misma transacción (commit).
- Si falla la persistencia de la evidencia previa, se hace rollback y NO se
  ejecuta el efecto (AuditAppendError).
- El hash encadena correctamente (recalculable con chain_hash).
"""
from argentgob.audit.append import AuditAppendError, AuditChainAppender
from argentgob.audit.canonical import GENESIS_HASH, canonical_payload, chain_hash
from tests.unit._h6_fakes import FakeConn


def _route_all(*args):
    return (0, None)


def _svc(route=_route_all):
    return AuditChainAppender(connection_factory=lambda: FakeConn(route))


def _head_route(head):
    """Route que devuelve una cabeza dada para el SELECT de head."""

    def route(sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            return (1, head)
        if "INSERT INTO audit_chain" in sql:
            return (1, None)
        return (0, None)

    return route


def test_append_primera_entrada_usa_genesis():
    from datetime import datetime, timezone

    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    records = {"sql": [], "params": []}

    def route(sql, params=None):
        records["sql"].append(sql)
        records["params"].append(params or {})
        if "ORDER BY sequence DESC" in sql:
            return (0, None)  # cadena vacía
        if "INSERT INTO audit_chain" in sql:
            return (1, None)
        return (0, None)

    svc = _svc(route)
    seq = svc.append(
        event_id="ev-1",
        event_type="tool_call",
        entity="stock_price",
        digest="abc",
        outcome="EXECUTED",
        actor="agent-1",
        timestamp=ts,
    )
    assert seq == 1
    insert_params = records["params"][-1]
    assert insert_params["sequence"] == 1
    assert insert_params["prev_hash"] == GENESIS_HASH
    # El chain_hash encadena contra el génesis.
    payload = canonical_payload(
        event_type="tool_call",
        entity="stock_price",
        digest="abc",
        outcome="EXECUTED",
        actor="agent-1",
        timestamp=ts,
    )
    assert insert_params["chain_hash"] == chain_hash(GENESIS_HASH, payload)


def test_append_entrada_siguiente_encadena_con_head():
    head = {"sequence": 5, "chain_hash": "sha256:" + "a" * 64}
    records = {"params": []}

    def route(sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            return (1, head)
        if "INSERT INTO audit_chain" in sql:
            records["params"].append(params or {})
            return (1, None)
        return (0, None)

    svc = _svc(route)
    seq = svc.append(
        event_id="ev-2",
        event_type="tool_call",
        entity="news",
        digest="def",
        outcome="EXECUTED",
        actor="agent-1",
    )
    assert seq == 6
    insert_params = records["params"][-1]
    assert insert_params["sequence"] == 6
    assert insert_params["prev_hash"] == head["chain_hash"]


def test_append_commit_transaccional():
    conn = FakeConn(_route_all)

    def route(sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            return (0, None)
        if "INSERT INTO audit_chain" in sql:
            return (1, None)
        return (0, None)

    conn.route = route
    svc = AuditChainAppender(connection_factory=lambda: conn)
    svc.append(
        event_id="ev-3",
        event_type="tool_call",
        entity="stock_price",
        digest="ghi",
        outcome="EXECUTED",
        actor="agent-1",
    )
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.closed is True


def test_append_rollback_si_falla_insercion():
    conn = FakeConn(_route_all)

    def route(sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            return (0, None)
        if "INSERT INTO audit_chain" in sql:
            raise RuntimeError("db down")
        return (0, None)

    conn.route = route
    svc = AuditChainAppender(connection_factory=lambda: conn)
    try:
        svc.append(
            event_id="ev-4",
            event_type="tool_call",
            entity="stock_price",
            digest="jkl",
            outcome="EXECUTED",
            actor="agent-1",
        )
        assert False, "debería lanzar"
    except RuntimeError:
        pass
    assert conn.rolled_back is True
    assert conn.committed is False


def test_append_restaura_autocommit():
    conn = FakeConn(_route_all)

    def route(sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            return (0, None)
        if "INSERT INTO audit_chain" in sql:
            return (1, None)
        return (0, None)

    conn.route = route
    conn.autocommit = True
    svc = AuditChainAppender(connection_factory=lambda: conn)
    svc.append(
        event_id="ev-5",
        event_type="tool_call",
        entity="stock_price",
        digest="mno",
        outcome="EXECUTED",
        actor="agent-1",
    )
    assert conn.autocommit is True


def test_append_decision_id_y_action_opcionales():
    records = {"params": []}

    def route(sql, params=None):
        if "ORDER BY sequence DESC" in sql:
            return (0, None)
        if "INSERT INTO audit_chain" in sql:
            records["params"].append(params or {})
            return (1, None)
        return (0, None)

    svc = _svc(route)
    svc.append(
        event_id="ev-6",
        event_type="tool_call",
        entity="stock_price",
        digest="pqr",
        outcome="EXECUTED",
        actor="agent-1",
        decision_id="dec-1",
        action="HITL",
    )
    insert_params = records["params"][-1]
    assert insert_params["decision_id"] == "dec-1"
    assert insert_params["action"] == "HITL"