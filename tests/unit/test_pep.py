"""
Tests del PEP (Policy Enforcement Point) — Módulo A.

Verifican los invariantes fundamentales del enforcement:
- INV-01: la herramienta nunca se ejecuta antes de una decisión válida.
- INV-02: un veredicto BLOCK aborta la tentativa (call_count permanece en 0).

Se usan mocks del ABACEvaluator y del AuditWriter para aislar el enforcement de
la base de datos.
"""
from unittest.mock import MagicMock

import pytest

from argentgob.core.decision import PolicyDecision
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    Obligation,
    ReasonCode,
)
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.guardrails import GuardrailEngine


def _decision(action: GovernanceAction, reason: ReasonCode) -> PolicyDecision:
    # R2 — Frontera gobernada: una decisión PASS exige exactamente una
    # obligación de selección de argumentos (por defecto, la original).
    obligations = (
        [Obligation.USE_ORIGINAL_ARGUMENTS]
        if action == GovernanceAction.PASS
        else []
    )
    return PolicyDecision(
        action=action, reason_code=reason, obligations=obligations
    )


def _middleware(settings, abac_decision, mock_audit_writer, guards=None):
    """Arma un GovernanceMiddleware con ABAC mockeado y guardrails opcionales."""
    abac = MagicMock()
    abac.evaluate = MagicMock(return_value=abac_decision)
    guardrails = GuardrailEngine(
        profile_guardrails=guards or [],
        rate_limit=settings.rate_limit_calls_per_run,
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=mock_audit_writer,
    )


def test_pass_ejecuta_la_herramienta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """Una decisión PASS permite ejecutar la herramienta (INV-01)."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    result = spy._run(query="AAPL")
    assert spy.call_count == 1
    assert result == "spy_executed:1"
    mock_audit_writer.record_decision.assert_called()
    mock_audit_writer.record_result.assert_called()


def test_block_por_politica_no_ejecuta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """INV-02: una decisión BLOCK por política impide la ejecución."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.BLOCK, ReasonCode.TOOL_NOT_ALLOWED),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0  # nunca se ejecutó
    assert "TOOL_NOT_ALLOWED" in exc.value.reason


def test_block_por_guardrail_no_ejecuta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """Un guardrail que bloquea aborta antes de ejecutar la herramienta."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        mock_audit_writer,
        guards=["query_injection"],
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="'; DROP TABLE policies;--")
    assert spy.call_count == 0
    assert "INJECTION_PATTERN_DETECTED" in exc.value.reason


def test_block_por_payload_limit_no_ejecuta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """INV-14: un payload que supera el límite se bloquea antes de ejecutar."""
    settings.max_payload_bytes = 10  # límite artificialmente bajo
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="una consulta bastante larga que supera el limite")
    assert spy.call_count == 0
    assert "PAYLOAD_LIMIT_EXCEEDED" in exc.value.reason


def test_hook_aborted_tiene_source(
    settings, agent_identity_analyst, mock_audit_writer
):
    """La excepción HookAborted expone reason y source."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.BLOCK, ReasonCode.TOOL_NOT_ALLOWED),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert exc.value.source == "argentgob.pep"


def test_sin_gobernanza_ejecuta_directo(agent_identity_analyst):
    """Sin middleware configurado, la herramienta se ejecuta directo (solo tests)."""
    spy = SpyTool()
    result = spy._run(query="AAPL")
    assert spy.call_count == 1
    assert result == "spy_executed:1"
