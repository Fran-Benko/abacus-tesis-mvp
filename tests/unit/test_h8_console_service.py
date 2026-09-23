"""Tests de H8 — Unidad 1: consultas sanitizadas de la consola.

Cubren:
- `list_decisions` con filtros parametrizados (acción, estado, tool, riesgo).
- `metrics` con conteos PASS/BLOCK/HITL, percentiles y errores.
- `pending_approvals` / `expired_approvals`.
- `audit_status` (íntegra / motivo sanitizado / error de infraestructura).
- No se exponen `execution_arguments` ni payload crudo (INV-05).
"""
import pytest

from argentgob.console.service import ConsoleQueryService


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


def _decision_row(action="PASS", status="SUCCESS", tool="stock_price", risk="LOW"):
    return {
        "decision_id": f"dec-{action}",
        "event_id": f"ev-{action}",
        "agent_id": "agent-1",
        "agent_role": "analyst",
        "tool_name": tool,
        "operation_class": "read",
        "resource": "market",
        "environment": "LOCAL",
        "payload_digest": "sha256:abc",
        "action": action,
        "risk_level": risk,
        "reason_codes": ["ALLOWED"],
        "policy_id": "pol-1",
        "policy_version": 1,
        "effective_priority": 50,
        "conflicts": [],
        "obligations": [],
        "decided_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "status": status,
        "error_digest": None,
        "result_digest": "sha256:res",
        "latency_post_ms": 12,
        "recorded_at": "2026-01-01T00:00:01+00:00",
    }


def _approval_row(approval_id="appr-1", state="PENDING", expires=None):
    return {
        "approval_id": approval_id,
        "event_id": "ev-1",
        "decision_id": "dec-1",
        "agent_id": "agent-1",
        "tool_name": "stock_price",
        "argument_digest": "sha256:arg",
        "idempotency_key": "idem-1",
        "state": state,
        "argued_at": "2026-01-01T00:00:00+00:00",
        "expires_at": expires,
    }


def _service(route, verifier=None):
    return ConsoleQueryService(
        connection_factory=lambda: _MemConn(route),
        verifier=verifier,
    )


def test_list_decisions_sin_filtros():
    rows = [_decision_row("PASS"), _decision_row("BLOCK")]
    svc = _service(lambda sql, params=None: rows)
    result = svc.list_decisions()
    assert len(result) == 2
    # No expone execution_arguments ni payload crudo.
    assert all("execution_arguments" not in r for r in result)
    assert all("payload" not in r for r in result)


def test_list_decisions_filtro_action_parametrizado():
    captured = {}

    def route(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [_decision_row("BLOCK")]

    svc = _service(route)
    result = svc.list_decisions(action="BLOCK")
    assert len(result) == 1
    assert result[0]["action"] == "BLOCK"
    # El filtro se pasa como parámetro, no interpolado.
    assert "action = %(action)s" in captured["sql"]
    assert captured["params"]["action"] == "BLOCK"


def test_list_decisions_filtro_status_y_tool():
    captured = {}

    def route(sql, params=None):
        captured["params"] = params
        return [_decision_row("PASS", status="FAILED")]

    svc = _service(route)
    result = svc.list_decisions(status="FAILED", tool_name="news")
    assert len(result) == 1
    assert captured["params"]["status"] == "FAILED"
    assert captured["params"]["tool_name"] == "news"


def test_metrics_conteos_y_percentiles():
    counts = {
        "total_pass": 5,
        "total_block": 2,
        "total_hitl": 1,
        "total": 8,
        "total_failed": 1,
        "total_unknown": 1,
        "total_no_result": 0,
    }
    pct = {"p50": 10.0, "p90": 20.0, "p99": 30.0}
    calls = {"n": 0}

    def route(sql, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [counts]
        return [pct]

    svc = _service(route)
    m = svc.metrics()
    assert m["pass"] == 5
    assert m["block"] == 2
    assert m["hitl"] == 1
    assert m["total"] == 8
    assert m["failed"] == 1
    assert m["unknown"] == 1
    assert m["latency_ms"]["p50"] == 10.0
    assert m["latency_ms"]["p99"] == 30.0


def test_pending_approvals():
    rows = [_approval_row("appr-1"), _approval_row("appr-2")]
    svc = _service(lambda sql, params=None: rows)
    result = svc.pending_approvals()
    assert len(result) == 2
    # No expone argumentos en claro.
    assert all("argument_digest" in r for r in result)
    assert all("execution_arguments" not in r for r in result)


def test_expired_approvals():
    rows = [_approval_row("appr-1", expires="2026-01-01T00:00:00+00:00")]
    svc = _service(lambda sql, params=None: rows)
    result = svc.expired_approvals()
    assert len(result) == 1
    assert result[0]["approval_id"] == "appr-1"


def test_audit_status_integra():
    class _Verifier:
        def verify(self):
            return (True, None, None)

    svc = _service(lambda sql, params=None: [], verifier=_Verifier())
    assert svc.audit_status() == {"ok": True, "reason": None, "sequence": None}


def test_audit_status_manipulada():
    class _Verifier:
        def verify(self):
            return (False, "hash_mismatch", 2)

    svc = _service(lambda sql, params=None: [], verifier=_Verifier())
    status = svc.audit_status()
    assert status["ok"] is False
    assert status["reason"] == "hash_mismatch"
    assert status["sequence"] == 2


def test_audit_status_error_infraestructura():
    class _Verifier:
        def verify(self):
            raise RuntimeError("db down")

    svc = _service(lambda sql, params=None: [], verifier=_Verifier())
    status = svc.audit_status()
    assert status["ok"] is False
    assert status["reason"] == "verification_error"