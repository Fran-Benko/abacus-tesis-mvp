"""Tests de observación de fronteras y correlación de run en el PEP (AV-01).

Verifican que el middleware emite eventos por allowlist sin alterar la decisión
de negocio, que `session_id` correlaciona con el `run_id` y que un observador
caído no rompe el enforcement.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    Obligation,
    ReasonCode,
)
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.guardrails import GuardrailEngine


class _RecordingObserver:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def on_governance_event(self, kind, payload):
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]


class _BrokenObserver:
    def on_governance_event(self, kind, payload):
        raise RuntimeError("observador caído")


def _decision(action: GovernanceAction, reason: ReasonCode) -> PolicyDecision:
    obligations = (
        [Obligation.USE_ORIGINAL_ARGUMENTS] if action == GovernanceAction.PASS else []
    )
    return PolicyDecision(action=action, reason_code=reason, obligations=obligations)


def _middleware(settings, decision, *, observer=None, run_id=None, guards=None):
    abac = MagicMock()
    abac.evaluate = MagicMock(return_value=decision)
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=GuardrailEngine(
            profile_guardrails=guards or [],
            rate_limit=settings.rate_limit_calls_per_run,
        ),
        audit=MagicMock(),
        observer=observer,
        run_id=run_id,
    )


def test_pass_emite_tentativa_decision_y_ejecucion(settings, agent_identity_analyst):
    """Un PASS emite tool.requested, governance.decided, tool.started y completed."""
    observer = _RecordingObserver()
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        observer=observer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    spy._run(query="AAPL")

    assert observer.kinds() == [
        "tool.requested",
        "governance.decided",
        "tool.started",
        "tool.completed",
    ]
    decided = dict(observer.events)["governance.decided"]
    assert decided["action"] == "PASS"
    assert decided["tool"] == "spy_tool"
    assert decided["reason_codes"] == ["ALLOWED"]


def test_block_emite_decision_y_no_ejecuta(settings, agent_identity_analyst):
    """Un BLOCK emite la decisión y nunca `tool.started` (INV-02)."""
    observer = _RecordingObserver()
    mw = _middleware(
        settings,
        _decision(GovernanceAction.BLOCK, ReasonCode.TOOL_NOT_ALLOWED),
        observer=observer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted):
        spy._run(query="AAPL")

    assert spy.call_count == 0
    assert "tool.started" not in observer.kinds()
    assert observer.kinds()[-1] == "governance.decided"


def test_guardrail_block_emite_motivo_sin_raw(settings, agent_identity_analyst):
    """El bloqueo por guardrail publica el nombre del guardrail, no el payload."""
    observer = _RecordingObserver()
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        observer=observer,
        guards=["query_injection"],
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted):
        spy._run(query="'; DROP TABLE users; --")

    decided = dict(observer.events)["governance.decided"]
    assert decided["action"] == "BLOCK"
    assert decided["reason_codes"] == ["INJECTION_PATTERN_DETECTED"]
    assert "DROP TABLE" not in str(decided)


def test_observador_caido_no_cambia_la_decision(settings, agent_identity_analyst):
    """Una excepción del observador no altera el PASS ni la ejecución."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        observer=_BrokenObserver(),
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    assert spy._run(query="AAPL") == "spy_executed:1"
    assert spy.call_count == 1


def test_run_id_correlaciona_session_id(settings, agent_identity_analyst):
    """Con `run_id`, el envelope usa ese valor como `session_id` (brecha 4)."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        run_id="run-correlado-1",
    )
    envelope = mw.pre_hook(
        agent=agent_identity_analyst,
        tool_name="spy_tool",
        operation_class="READ",
        resource="test",
        execution_arguments={"query": "AAPL"},
    )
    assert envelope.session_id == "run-correlado-1"


def test_sin_run_id_se_conserva_session_id_aleatorio(settings, agent_identity_analyst):
    """Sin run (CLI/tests) el comportamiento histórico no cambia."""
    env1 = ToolCallEnvelope.build(
        agent=agent_identity_analyst,
        tool_name="spy_tool",
        operation_class="READ",
        resource="test",
        environment="TEST",
        execution_arguments={"query": "AAPL"},
    )
    env2 = ToolCallEnvelope.build(
        agent=agent_identity_analyst,
        tool_name="spy_tool",
        operation_class="READ",
        resource="test",
        environment="TEST",
        execution_arguments={"query": "AAPL"},
    )
    assert env1.session_id != env2.session_id


def test_observer_por_defecto_es_noop(settings, agent_identity_analyst):
    """Sin observador inyectado, el PEP funciona igual que antes."""
    mw = _middleware(
        settings, _decision(GovernanceAction.PASS, ReasonCode.ALLOWED)
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    assert spy._run(query="AAPL") == "spy_executed:1"
