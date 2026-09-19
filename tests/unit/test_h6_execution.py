"""Tests de H6 — Unidad 5: ejecucion gobernada (ExecutionOrchestrator).

Cubren:
- La capability de un solo uso se consume ANTES de invocar al executor.
- Capability no consumible (reusada o vencida) -> no ejecuta, lanza HitlError.
- SUCCESS y FAILED se persisten en execution_results.
- Ante error del executor se registra FAILED.
"""
import pytest

from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.hitl.errors import HitlError
from argentgob.hitl.execution import ExecutionOrchestrator
from argentgob.hitl.records import ExecutionCapability
from tests.unit._h6_fakes import FakeConn


def _settings():
    from argentgob.core.config import Settings

    return Settings(
        environment="TEST",
        log_level="WARNING",
        max_payload_bytes=65536,
    )


def _envelope(event_id="ev-1", args=None):
    return ToolCallEnvelope.build(
        agent=AgentIdentity(id="agent-1", role="analyst"),
        tool_name="stock_price",
        operation_class="READ",
        resource="yahoo_finance",
        environment="TEST",
        execution_arguments=args or {"query": "AAPL"},
    )


def _capability(**over):
    cap = ExecutionCapability(
        event_id="ev-1",
        approval_id="appr-1",
        tool_name="stock_price",
        argument_digest="dig-abc",
        expires_at=None,
    )
    for k, v in over.items():
        setattr(cap, k, v)
    return cap


def _orch(route):
    return ExecutionOrchestrator(
        settings=_settings(),
        connection_factory=lambda: FakeConn(route),
    )


def test_execute_success_persiste_y_consume_cap():
    inserted = []

    def route(sql, params=None):
        if "INSERT INTO execution_results" in sql:
            inserted.append(params.get("status"))
            return (1, None)
        return (0, None)

    orch = _orch(route)
    cap = _capability()
    result = orch.execute_governed(
        capability=cap,
        envelope=_envelope(),
        executor=lambda args: "OK",
    )
    assert result == "governed:SUCCESS"
    assert cap.consumed is True  # se consumio antes de ejecutar
    assert inserted == ["SUCCESS"]


def test_execute_failed_persiste_failed():
    inserted = []

    def route(sql, params=None):
        if "INSERT INTO execution_results" in sql:
            inserted.append(params.get("status"))
            return (1, None)
        return (0, None)

    orch = _orch(route)

    def boom(args):
        raise RuntimeError("boom")

    result = orch.execute_governed(
        capability=_capability(),
        envelope=_envelope(),
        executor=boom,
    )
    assert result == "governed:FAILED"
    assert inserted == ["FAILED"]


def test_execute_capability_ya_consumida_no_ejecuta():
    def route(sql, params=None):
        return (0, None)

    orch = _orch(route)
    cap = _capability()
    assert cap.claim() is True  # consumida por un primer uso
    with pytest.raises(HitlError):
        orch.execute_governed(
            capability=cap,
            envelope=_envelope(),
            executor=lambda args: "NUNCA",
        )


def test_execute_capability_vencida_no_ejecuta():
    from datetime import datetime, timedelta, timezone

    def route(sql, params=None):
        return (0, None)

    orch = _orch(route)
    cap = _capability(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(HitlError):
        orch.execute_governed(
            capability=cap,
            envelope=_envelope(),
            executor=lambda args: "NUNCA",
        )


def test_execute_sin_capability_lanza():
    orch = _orch(lambda sql, params=None: (0, None))
    with pytest.raises(HitlError):
        orch.execute_governed(
            capability=None,
            envelope=_envelope(),
            executor=lambda args: "OK",
        )