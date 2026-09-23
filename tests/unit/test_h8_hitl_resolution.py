"""Tests de H8 — Unidad 1/3: detalle HITL y resolución autorizada.

Cubren:
- `HitlDetailService.get_detail` devuelve campos sanitizados (sin argumentos
  en claro) y calcula edad/expiración.
- `ApprovalNotFoundError` si no existe.
- `ResolutionService.resolve` exige evidencia vigente + rol auditor.
- Distingue APPROVED de SUCCESS/FAILED/UNKNOWN (aprobar no garantiza éxito).
- Maneja la carrera (dos sesiones, solo una gana) y el hold perdido.
"""
from datetime import datetime, timedelta, timezone

import pytest

from argentgob.console.hitl_detail import ApprovalNotFoundError, HitlDetailService
from argentgob.console.resolution import ResolutionService
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole
from argentgob.hitl.errors import ApprovalNotPendingError
from argentgob.hitl.resolver import ApprovalResolver


class _MemCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = 0

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
    def __init__(self, route):
        self.autocommit = True
        self.route = route
        self.record = []
        self.closed = False

    def cursor(self, row_factory=None):
        return _MemCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _detail_row(state="PENDING", argued=None, expires=None, resolved_by=None):
    return {
        "approval_id": "appr-1",
        "event_id": "ev-1",
        "decision_id": "dec-1",
        "agent_id": "agent-1",
        "tool_name": "stock_price",
        "argument_digest": "sha256:arg",
        "idempotency_key": "idem-1",
        "state": state,
        "argued_at": argued or datetime.now(timezone.utc) - timedelta(minutes=5),
        "expires_at": expires,
        "resolved_at": None,
        "resolved_by": resolved_by,
        "decision": None,
        "resource": "market",
        "environment": "LOCAL",
        "risk_level": "HIGH",
        "reason_codes": ["HUMAN_APPROVAL_REQUIRED"],
        "policy_id": "pol-1",
        "policy_version": 1,
        "operation_class": "read",
    }


def _detail_service(route):
    return HitlDetailService(connection_factory=lambda: _MemConn(route))


def _identity_provider():
    return AuditorIdentityProvider(
        environment="TEST",
        development_only=True,
        fixture_roles={"auditor-1": ["viewer", "auditor"]},
    )


def _evidence(provider, identifier="auditor-1"):
    return provider.authenticate(identifier=identifier, role=AppRole.AUDITOR)


def _resolution_service(provider, resolver, detail):
    return ResolutionService(
        identity_provider=provider,
        resolver=resolver,
        detail_service=detail,
    )


# ── HitlDetailService ───────────────────────────────────────────────
def test_detail_sanitizado_sin_argumentos_en_claro():
    svc = _detail_service(lambda sql, params=None: [_detail_row()])
    det = svc.get_detail(approval_id="appr-1")
    assert det["approval_id"] == "appr-1"
    assert det["tool_name"] == "stock_price"
    assert det["risk_level"] == "HIGH"
    assert det["policy_id"] == "pol-1"
    assert det["policy_version"] == 1
    # Solo digest, nunca argumentos en claro.
    assert det["argument_digest"] == "sha256:arg"
    assert "execution_arguments" not in det
    assert "payload" not in det
    # Edad calculada.
    assert det["age_seconds"] is not None
    assert det["age_seconds"] >= 0


def test_detail_no_existe():
    svc = _detail_service(lambda sql, params=None: [])
    with pytest.raises(ApprovalNotFoundError):
        svc.get_detail(approval_id="nope")


def test_detail_expira_calcula_expires_in():
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    svc = _detail_service(lambda sql, params=None: [_detail_row(expires=expires)])
    det = svc.get_detail(approval_id="appr-1")
    assert det["expires_in_seconds"] is not None
    assert det["expires_in_seconds"] >= 0


# ── ResolutionService ───────────────────────────────────────────────
class _FakeResolver:
    """Resolver falso que registra llamadas y devuelve un resultado fijo."""

    def __init__(self, applied=True, exc=None):
        self.applied = applied
        self.exc = exc
        self.calls = []

    def resolve(self, *, approval, auditor, decision, evidence=None):
        self.calls.append((approval, auditor, decision, evidence))
        if self.exc:
            raise self.exc
        return self.applied


def test_resolve_aprueba_con_evidencia_vigente():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [_detail_row()])
    resolver = _FakeResolver(applied=True)
    svc = _resolution_service(provider, resolver, detail)
    result = svc.resolve(
        approval_id="appr-1",
        decision="APPROVED",
        evidence=_evidence(provider),
    )
    assert result["outcome"] == "approved"
    assert result["decision"] == "APPROVED"
    assert result["resolved_by"] == "auditor-1"
    # El resolver recibió identidad con rol auditor.
    _, auditor, decision, _ = resolver.calls[0]
    assert auditor.role == "auditor"
    assert auditor.id == "auditor-1"
    assert decision == "APPROVED"


def test_resolve_rechaza_sin_evidencia():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [_detail_row()])
    resolver = _FakeResolver(applied=True)
    svc = _resolution_service(provider, resolver, detail)
    result = svc.resolve(
        approval_id="appr-1", decision="APPROVED", evidence=None
    )
    assert result["outcome"] == "blocked"
    assert result["reason"] == "unauthorized"
    assert resolver.calls == []  # nunca llegó al resolver


def test_resolve_rechaza_evidencia_vencida():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [_detail_row()])
    resolver = _FakeResolver(applied=True)
    svc = _resolution_service(provider, resolver, detail)
    ev = _evidence(provider)
    ev.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    result = svc.resolve(approval_id="appr-1", decision="APPROVED", evidence=ev)
    assert result["outcome"] == "blocked"
    assert result["reason"] == "unauthorized"


def test_resolve_rechaza_rol_no_autorizado():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [_detail_row()])
    resolver = _FakeResolver(applied=True)
    svc = _resolution_service(provider, resolver, detail)
    # actor sin rol auditor
    ev = provider.authenticate(identifier="viewer-1", role=AppRole.VIEWER)
    result = svc.resolve(approval_id="appr-1", decision="APPROVED", evidence=ev)
    assert result["outcome"] == "blocked"
    assert result["reason"] == "unauthorized"


def test_resolve_hold_perdido_no_falso_exito():
    provider = _identity_provider()
    detail = _detail_service(
        lambda sql, params=None: [_detail_row(state="APPROVED")]
    )
    resolver = _FakeResolver(applied=True)
    svc = _resolution_service(provider, resolver, detail)
    result = svc.resolve(
        approval_id="appr-1",
        decision="APPROVED",
        evidence=_evidence(provider),
    )
    assert result["outcome"] == "already_resolved"
    assert result["state"] == "APPROVED"
    assert resolver.calls == []  # no se re-resuelve


def test_resolve_carrera_solo_una_gana():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [_detail_row()])
    # El resolver lanza ApprovalNotPendingError: otra sesión ganó.
    resolver = _FakeResolver(exc=ApprovalNotPendingError("ya resuelta"))
    svc = _resolution_service(provider, resolver, detail)
    result = svc.resolve(
        approval_id="appr-1",
        decision="APPROVED",
        evidence=_evidence(provider),
    )
    assert result["outcome"] == "already_resolved"
    assert result["reason"] == "race_or_expired"


def test_resolve_no_aplicado_es_ya_resuelto():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [_detail_row()])
    resolver = _FakeResolver(applied=False)
    svc = _resolution_service(provider, resolver, detail)
    result = svc.resolve(
        approval_id="appr-1",
        decision="APPROVED",
        evidence=_evidence(provider),
    )
    assert result["outcome"] == "already_resolved"


def test_resolve_no_existe():
    provider = _identity_provider()
    detail = _detail_service(lambda sql, params=None: [])
    resolver = _FakeResolver(applied=True)
    svc = _resolution_service(provider, resolver, detail)
    result = svc.resolve(
        approval_id="nope",
        decision="APPROVED",
        evidence=_evidence(provider),
    )
    assert result["outcome"] == "blocked"
    assert result["reason"] == "not_found"