"""Tests de H6 — Unidad 2: creacion durable de approvals (ApprovalService).

Cubren el flujo transaccional `create_pending_and_issue_token` con una
conexion falsa (route inyectable):

- Hold registrado -> emite token de resume (token_id + token_plan opaco).
- Hold NO registrado -> la approval se cancela y NO se emite token.
- El token plano nunca se persiste; solo su hash sha256 en la DB.
- Orden critico: el INSERT de la approval ocurre antes de la emision del token.
"""
from datetime import datetime, timedelta, timezone

from argentgob.hitl.approval_service import ApprovalService
from argentgob.hitl.tokens import hash_token
from tests.unit._h6_fakes import FakeConn


def _route_all(*args):
    """Route por defecto: no produce filas y no afecta nada."""
    return (0, None)


def _future():
    return datetime.now(timezone.utc) + timedelta(seconds=300)


def _svc(route=_route_all):
    return ApprovalService(settings=None, connection_factory=lambda: FakeConn(route))


def test_approval_emite_token_cuando_hold_ok(settings):
    records = {"sql": [], "params": []}

    def route(sql, params=None):
        records["sql"].append(sql)
        records["params"].append(params or {})
        if "INSERT INTO approval_requests" in sql:
            return (1, {
                "approval_id": "appr-1",
                "event_id": "ev-1",
                "decision_id": "dec-1",
                "agent_id": "agent-1",
                "tool_name": "stock_price",
                "argument_digest": "abc123",
                "idempotency_key": "hitl:ev-1",
                "state": "PENDING",
                "argued_at": None,
                "expires_at": None,
                "resolved_at": None,
                "resolved_by": None,
                "decision": None,
            })
        if "INSERT INTO approval_resume_tokens" in sql:
            return (1, None)
        return (0, None)

    svc = ApprovalService(
        settings=settings,
        connection_factory=lambda: FakeConn(route),
    )
    approval, token_id, token_plan = svc.create_pending_and_issue_token(
        event_id="ev-1",
        decision_id="dec-1",
        agent_id="agent-1",
        tool_name="stock_price",
        argument_digest="abc123",
        idempotency_key="hitl:ev-1",
        argued_at=datetime.now(timezone.utc),
        expires_at=_future(),
        hold_registered=lambda: True,
    )
    assert approval.approval_id == "appr-1"
    assert approval.state == "PENDING"
    assert token_id is not None and token_plan is not None
    # El orden critico: se inserta la approval antes que el token.
    def _first(sub):
        return next(i for i, s in enumerate(records["sql"]) if sub in s)

    assert _first("INSERT INTO approval_resume_tokens") > _first(
        "INSERT INTO approval_requests"
    )
    # El valor plano nunca se escribe: solo su hash en el INSERT del token.
    serialized = str(records["sql"]) + str(records["params"])
    assert token_plan not in serialized
    assert any(hash_token(token_plan) in str(p) for p in records["params"])


def test_approval_no_emite_token_si_hold_falla(settings):
    calls = {"cancel": 0}

    def route(sql, params=None):
        if "UPDATE approval_requests SET state = 'CANCELLED'" in sql:
            calls["cancel"] += 1
            return (1, None)
        if "INSERT INTO approval_requests" in sql:
            return (1, {
                "approval_id": "appr-2",
                "event_id": "ev-2",
                "decision_id": None,
                "agent_id": None,
                "tool_name": "stock_price",
                "argument_digest": "abc",
                "idempotency_key": "hitl:ev-2",
                "state": "PENDING",
            })
        return (0, None)

    svc = _svc(route)
    approval, token_id, token_plan = svc.create_pending_and_issue_token(
        event_id="ev-2",
        decision_id=None,
        agent_id=None,
        tool_name="stock_price",
        argument_digest="abc",
        idempotency_key="hitl:ev-2",
        argued_at=datetime.now(timezone.utc),
        expires_at=_future(),
        hold_registered=lambda: False,
    )
    assert approval is not None
    assert token_id is None and token_plan is None
    assert calls["cancel"] == 1


def test_approval_idempotente_por_idempotency_key(settings):
    def route(sql, params=None):
        if "INSERT INTO approval_requests" in sql:
            # ON CONFLICT devuelve la fila existente con su estado original.
            return (1, {
                "approval_id": "appr-exist",
                "event_id": "ev-9",
                "decision_id": None,
                "agent_id": None,
                "tool_name": "stock_price",
                "argument_digest": "abc",
                "idempotency_key": "hitl:ev-9",
                "state": "PENDING",
            })
        if "INSERT INTO approval_resume_tokens" in sql:
            return (1, None)
        return (0, None)

    svc = _svc(route)
    a1, t1, p1 = svc.create_pending_and_issue_token(
        event_id="ev-9", decision_id=None, agent_id=None,
        tool_name="stock_price", argument_digest="abc",
        idempotency_key="hitl:ev-9",
        argued_at=datetime.now(timezone.utc), expires_at=_future(),
        hold_registered=lambda: True,
    )
    a2, t2, p2 = svc.create_pending_and_issue_token(
        event_id="ev-9", decision_id=None, agent_id=None,
        tool_name="stock_price", argument_digest="abc",
        idempotency_key="hitl:ev-9",
        argued_at=datetime.now(timezone.utc), expires_at=_future(),
        hold_registered=lambda: True,
    )
    assert a1.approval_id == a2.approval_id == "appr-exist"
