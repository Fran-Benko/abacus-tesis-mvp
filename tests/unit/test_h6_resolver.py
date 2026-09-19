"""Tests de H6 — Unidad 3: resolucion por auditor autorizado (ApprovalResolver).

Cubren:
- Auditor autorizado resuelve APPROVED/REJECTED de forma transaccional.
- Auditor NO autorizado lanza UnauthorizedAuditorError.
- Decision invalida lanza ApprovalNotPendingError.
- Ya resuelta / digest incorrecto -> ApprovalNotPendingError (no re-resuelve).
"""
import pytest

from argentgob.core.envelope import AgentIdentity
from argentgob.hitl.errors import (
    ApprovalNotPendingError,
    TargetNotFoundError,
    UnauthorizedAuditorError,
)
from argentgob.hitl.resolver import ApprovalResolver
from tests.unit._h6_fakes import FakeConn


def _auditor(role="admin"):
    return AgentIdentity(id="auditor-1", role=role)


def _approval(**over):
    base = {"approval_id": "appr-1", "argument_digest": "dig-abc"}
    base.update(over)
    return base


def _conn_state(initial="PENDING", digest="dig-abc", expires=None, exists=True):
    """Conexion falsa cuyo UPDATE de resolucion aplica si la approval esta PENDING."""
    import datetime as _dt

    state = {"value": initial}

    def route(sql, params=None):
        if "UPDATE approval_requests" in sql:
                # Si la approval no existe no debe haber fila que actualizar:
                # solo diagnostica (SELECT) el caso de fila inexistente.
                if not exists:
                    return (0, None)
                if state["value"] == "PENDING":
                    state["value"] = params.get("decision")
                    return (1, None)
                return (0, None)
        if "SELECT state, argument_digest, resolved_at, expires_at" in sql:
            if not exists:
                return (0, None)
            return (0, ("APPROVED", digest, None, expires))
        return (0, None)

    return FakeConn(route), state


def test_resolver_autorizado_aplica_approved(settings):
    conn, state = _conn_state("PENDING")
    resolver = ApprovalResolver(
        settings=settings,
        auditor_roles=["admin"],
        connection_factory=lambda: conn,
    )
    applied = resolver.resolve(
        approval=_approval(),
        auditor=_auditor("admin"),
        decision="APPROVED",
    )
    assert applied is True
    assert state["value"] == "APPROVED"


def test_resolver_rechaza_auditor_no_autorizado(settings):
    conn, _ = _conn_state("PENDING")
    resolver = ApprovalResolver(
        settings=settings,
        auditor_roles=["admin"],
        connection_factory=lambda: conn,
    )
    with pytest.raises(UnauthorizedAuditorError):
        resolver.resolve(
            approval=_approval(),
            auditor=_auditor("analyst"),
            decision="APPROVED",
        )


def test_resolver_rechaza_decision_invalida(settings):
    conn, _ = _conn_state("PENDING")
    resolver = ApprovalResolver(
        settings=settings,
        auditor_roles=["admin"],
        connection_factory=lambda: conn,
    )
    with pytest.raises(ApprovalNotPendingError):
        resolver.resolve(
            approval=_approval(),
            auditor=_auditor("admin"),
            decision="MAYBE",
        )


def test_resolver_ya_resuelta_no_re_resuelve(settings):
    conn, state = _conn_state("APPROVED")
    resolver = ApprovalResolver(
        settings=settings,
        auditor_roles=["admin"],
        connection_factory=lambda: conn,
    )
    with pytest.raises(ApprovalNotPendingError):
        resolver.resolve(
            approval=_approval(),
            auditor=_auditor("admin"),
            decision="REJECTED",
        )
    assert state["value"] == "APPROVED"  # no se piso


def test_resolver_approval_inexistente_target_not_found(settings):
    conn, _ = _conn_state(exists=False)
    resolver = ApprovalResolver(
        settings=settings,
        auditor_roles=["admin"],
        connection_factory=lambda: conn,
    )
    with pytest.raises(TargetNotFoundError):
        resolver.resolve(
            approval=_approval(),
            auditor=_auditor("admin"),
            decision="APPROVED",
        )