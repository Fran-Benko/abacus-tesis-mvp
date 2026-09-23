"""Tests de integración de H8 — consola sanitizada y resolución autorizada.

Ejercitan el flujo completo de la consola con una conexión falsa en memoria
que simula las tablas/vistas de gobernanza. NO requieren PostgreSQL.

Cubren:
- Consulta sanitizada con filtros parametrizados (sin leak de canarios).
- Detalle HITL sanitizado (sin argumentos en claro).
- Resolución autorizada: aprobar con evidencia vigente aplica el cambio;
  una segunda sesión (carrera) no re-resuelve.
- Resolución sin evidencia/rol queda bloqueada.
"""
from datetime import datetime, timedelta, timezone

from argentgob.console.hitl_detail import HitlDetailService
from argentgob.console.resolution import ResolutionService
from argentgob.console.service import ConsoleQueryService
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole
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
        rowcount, rows = self.conn.route(sql, params)
        self.rowcount = rowcount
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MemConn:
    def __init__(self, store):
        self.autocommit = True
        self.store = store
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

    def route(self, sql, params=None):
        # UPDATE de resoluci?n (ApprovalResolver).
        if "UPDATE approval_requests" in sql:
            approval_id = params.get("approval_id")
            row = self.store["approvals"].get(approval_id)
            if row and row["state"] == "PENDING":
                row["state"] = params.get("decision")
                row["resolved_by"] = params.get("resolved_by")
                return (1, [])
            return (0, [])
        # Diagn?stico de conflicto (ApprovalResolver).
        if "SELECT state, argument_digest, resolved_at, expires_at" in sql:
            row = self.store["approvals"].get(params.get("approval_id"))
            if not row:
                return (0, [])
            return (0, [(row["state"], row["argument_digest"], None, row["expires_at"])])
        # Detalle HITL (join approval_requests + policy_decisions).
        if "FROM approval_requests ar" in sql:
            row = self.store["approvals"].get(params.get("approval_id"))
            if not row:
                return (0, [])
            dec = self.store["decisions"].get(row["decision_id"], {})
            return (0, [{
                "approval_id": row["approval_id"],
                "event_id": row["event_id"],
                "decision_id": row["decision_id"],
                "agent_id": row["agent_id"],
                "tool_name": row["tool_name"],
                "argument_digest": row["argument_digest"],
                "idempotency_key": row["idempotency_key"],
                "state": row["state"],
                "argued_at": row["argued_at"],
                "expires_at": row["expires_at"],
                "resolved_at": row["resolved_at"],
                "resolved_by": row["resolved_by"],
                "decision": row["decision"],
                "resource": dec.get("resource"),
                "environment": dec.get("environment"),
                "risk_level": dec.get("risk_level"),
                "reason_codes": dec.get("reason_codes"),
                "policy_id": dec.get("policy_id"),
                "policy_version": dec.get("policy_version"),
                "operation_class": dec.get("operation_class"),
            }])
        # Listado de la vista sanitizada.
        if "FROM v_governance_console" in sql:
            rows = list(self.store["decisions"].values())
            if params and params.get("action"):
                rows = [r for r in rows if r["action"] == params["action"]]
            return (0, rows)
        # Approvals PENDING.
        if "FROM approval_requests" in sql and "state = 'PENDING'" in sql:
            return (0, [
                r for r in self.store["approvals"].values()
                if r["state"] == "PENDING"
            ])
        return (0, [])


def _make_store():
    now = datetime.now(timezone.utc)
    return {
        "decisions": {
            "dec-1": {
                "decision_id": "dec-1",
                "event_id": "ev-1",
                "agent_id": "agent-1",
                "agent_role": "analyst",
                "tool_name": "stock_price",
                "operation_class": "read",
                "resource": "market",
                "environment": "LOCAL",
                "payload_digest": "sha256:abc",
                "action": "HITL",
                "risk_level": "HIGH",
                "reason_codes": ["HUMAN_APPROVAL_REQUIRED"],
                "policy_id": "pol-1",
                "policy_version": 1,
                "effective_priority": 50,
                "conflicts": [],
                "obligations": [],
                "decided_at": now,
                "expires_at": None,
                "status": None,
                "error_digest": None,
                "result_digest": None,
                "latency_post_ms": None,
                "recorded_at": now,
            },
            "dec-2": {
                "decision_id": "dec-2",
                "event_id": "ev-2",
                "agent_id": "agent-1",
                "agent_role": "analyst",
                "tool_name": "news",
                "operation_class": "read",
                "resource": "news",
                "environment": "LOCAL",
                "payload_digest": "sha256:def",
                "action": "PASS",
                "risk_level": "LOW",
                "reason_codes": ["ALLOWED"],
                "policy_id": "pol-2",
                "policy_version": 1,
                "effective_priority": 50,
                "conflicts": [],
                "obligations": [],
                "decided_at": now,
                "expires_at": None,
                "status": "SUCCESS",
                "error_digest": None,
                "result_digest": "sha256:res",
                "latency_post_ms": 15,
                "recorded_at": now,
            },
        },
        "approvals": {
            "appr-1": {
                "approval_id": "appr-1",
                "event_id": "ev-1",
                "decision_id": "dec-1",
                "agent_id": "agent-1",
                "tool_name": "stock_price",
                "argument_digest": "sha256:arg",
                "idempotency_key": "idem-1",
                "state": "PENDING",
                "argued_at": now - timedelta(minutes=5),
                "expires_at": now + timedelta(minutes=5),
                "resolved_at": None,
                "resolved_by": None,
                "decision": None,
            },
        },
    }


def _services(store):
    conn_factory = lambda: _MemConn(store)
    query = ConsoleQueryService(connection_factory=conn_factory)
    detail = HitlDetailService(connection_factory=conn_factory)
    resolver = ApprovalResolver(
        settings=None,
        auditor_roles=["auditor"],
        connection_factory=conn_factory,
    )
    identity = AuditorIdentityProvider(
        environment="TEST",
        development_only=True,
        fixture_roles={"auditor-1": ["viewer", "auditor"]},
    )
    resolution = ResolutionService(
        identity_provider=identity,
        resolver=resolver,
        detail_service=detail,
    )
    return query, detail, resolution, identity


def test_consulta_sanitizada_no_filtra_canarios():
    store = _make_store()
    query, _, _, _ = _services(store)
    rows = query.list_decisions()
    # No expone execution_arguments ni payload crudo.
    for r in rows:
        assert "execution_arguments" not in r
        assert "payload" not in r
    # Filtro por acción parametrizado.
    hitl = query.list_decisions(action="HITL")
    assert len(hitl) == 1
    assert hitl[0]["action"] == "HITL"


def test_detalle_hitl_sanitizado():
    store = _make_store()
    _, detail, _, _ = _services(store)
    det = detail.get_detail(approval_id="appr-1")
    assert det["tool_name"] == "stock_price"
    assert det["risk_level"] == "HIGH"
    assert det["argument_digest"] == "sha256:arg"
    assert "execution_arguments" not in det
    assert det["age_seconds"] is not None


def test_resolucion_autorizada_aplica_y_carrera_no_reejecuta():
    store = _make_store()
    _, _, resolution, identity = _services(store)
    evidence = identity.authenticate(identifier="auditor-1", role=AppRole.AUDITOR)

    # Primera sesión aprueba.
    r1 = resolution.resolve(
        approval_id="appr-1", decision="APPROVED", evidence=evidence
    )
    assert r1["outcome"] == "approved"
    assert store["approvals"]["appr-1"]["state"] == "APPROVED"

    # Segunda sesión (carrera): ya resuelta, no re-resuelve.
    r2 = resolution.resolve(
        approval_id="appr-1", decision="REJECTED", evidence=evidence
    )
    assert r2["outcome"] == "already_resolved"
    assert store["approvals"]["appr-1"]["state"] == "APPROVED"  # no se piso


def test_resolucion_sin_evidencia_bloqueada():
    store = _make_store()
    _, _, resolution, _ = _services(store)
    result = resolution.resolve(
        approval_id="appr-1", decision="APPROVED", evidence=None
    )
    assert result["outcome"] == "blocked"
    assert result["reason"] == "unauthorized"
    assert store["approvals"]["appr-1"]["state"] == "PENDING"  # sin cambios


def test_resolucion_rol_no_autorizado_bloqueada():
    store = _make_store()
    _, _, resolution, identity = _services(store)
    # actor sin rol auditor
    evidence = identity.authenticate(identifier="viewer-1", role=AppRole.VIEWER)
    result = resolution.resolve(
        approval_id="appr-1", decision="APPROVED", evidence=evidence
    )
    assert result["outcome"] == "blocked"
    assert result["reason"] == "unauthorized"
    assert store["approvals"]["appr-1"]["state"] == "PENDING"