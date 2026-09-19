"""Tests de H6 — Hook del middleware con servicios HITL inyectados.

Verifican que cuando `GovernanceMiddleware` recibe `hold`, `approval_service`
y `notifier`, una decision HITL:

1. Registra el hold de los argumentos (idempotente).
2. Crea la approval PENDING y emite un token de resume.
3. Notifica al humano.
4. Aborta con `blocked:HUMAN_APPROVAL_REQUIRED` (no ejecuta la herramienta).
5. Registra la decision en el auditor.

Tambien que sin servicios (degradacion R2) solo bloquea sin perseguir la
aprobacion humana (compatible con test_r2_frontera).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import GovernanceAction, HookAborted, ReasonCode
from argentgob.hitl.argument_hold import InMemoryArgumentHold
from argentgob.module_a.governance import GovernanceMiddleware, _argument_digest
from argentgob.module_b.argument_transforms import select_effective_arguments
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.guardrails import GuardrailEngine


def _decision(action=GovernanceAction.HITL, reason=ReasonCode.HUMAN_APPROVAL_REQUIRED):
    return PolicyDecision(action=action, reason_code=reason, obligations=[])


def _mw(settings, mock_audit_writer, *, hold=None, approval_service=None, notifier=None):
    abac = MagicMock()
    abac.evaluate = MagicMock(return_value=_decision())
    guards = GuardrailEngine(profile_guardrails=[], rate_limit=settings.rate_limit_calls_per_run)
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guards,
        audit=mock_audit_writer,
        hold=hold,
        approval_service=approval_service,
        notifier=notifier,
    )


class FakeApprovalService:
    def __init__(self):
        self.calls = []
        self.token = ("tok-1", "secret-token-plan")

    def create_pending_and_issue_token(self, **kw):
        self.calls.append(kw)
        # El servicio real invoca el hold_registered; el fake también,
        # para que el hook deje el hold registrado de verdad.
        hold_ok = kw["hold_registered"]()
        if not hold_ok:
            return MagicMock(approval_id="appr-1"), None, None
        return MagicMock(approval_id="appr-1"), self.token[0], self.token[1]


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify_approval_pending(self, **kw):
        self.calls.append(kw)


def test_hitl_con_servicios_registra_hold_emite_token_y_notifica(
    settings, agent_identity_analyst, mock_audit_writer
):
    hold = InMemoryArgumentHold(settings)
    appr = FakeApprovalService()
    notifier = FakeNotifier()
    mw = _mw(
        settings,
        mock_audit_writer,
        hold=hold,
        approval_service=appr,
        notifier=notifier,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0  # la herramienta NO se ejecuta
    assert "HUMAN_APPROVAL_REQUIRED" in exc.value.reason

    # El hold quedo registrado con el evento.
    event_id = appr.calls[0]["event_id"]
    assert event_id
    held = hold.get_with_digest(event_id)
    assert held is not None
    # La approval se creo pidiendo el digest correcto.
    call = appr.calls[0]
    assert call["argument_digest"] == _argument_digest({"query": "AAPL"})
    # Se notifico con el approval_id.
    assert notifier.calls and notifier.calls[0]["approval_id"] == "appr-1"
    # Auditoria registra una decision.
    assert mock_audit_writer.record_decision.call_count >= 1


def test_hitl_sin_servicios_solo_bloquea(
    settings, agent_identity_analyst, mock_audit_writer
):
    mw = _mw(settings, mock_audit_writer)  # sin hold/approval/notifier
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0
    assert "HUMAN_APPROVAL_REQUIRED" in exc.value.reason
    assert mock_audit_writer.record_decision.call_count >= 1