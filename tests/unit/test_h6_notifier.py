"""Tests de H6 — Notificacion de approvals pendientes (ApprovalNotifier).

Cubren:
- El notificador entrega SOLO informacion no sensible (nunca los argumentos
  crudos de ejecucion).
- Si el canal falla, se aborta de forma segura con NotificationError.
"""
from datetime import datetime, timezone

import pytest

from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.hitl.errors import NotificationError
from argentgob.hitl.notifier import ApprovalNotifier


def _envelope():
    envelope = ToolCallEnvelope.build(
        agent=AgentIdentity(id="agent-1", role="analyst"),
        tool_name="stock_price",
        operation_class="READ",
        resource="yahoo_finance",
        environment="TEST",
        execution_arguments={"query": "AAPL", "secret": "s3cr3t"},
    )
    # La telemetria (sanitizada) es lo unico que el notifier puede ver.
    envelope.telemetry_arguments = {"query": "AAPL", "secret": "***REDACTED***"}
    return envelope


class RecordingChannel:
    def __init__(self, ok=True):
        self.ok = ok
        self.received = None

    def notify(self, *, notification, destination=None):
        if not self.ok:
            return False
        self.received = notification
        return True


def test_notifier_envia_solo_datos_no_sensibles():
    channel = RecordingChannel(ok=True)
    notifier = ApprovalNotifier(channel)
    env = _envelope()
    notifier.notify_approval_pending(
        envelope=env,
        approval_id="appr-1",
        idempotency_key="hitl:ev-1",
        expires_at=datetime.now(timezone.utc),
        destination="auditor@example.com",
        sanitized_preview=env.telemetry_arguments,
    )
    payload = channel.received
    assert payload["kind"] == "approval_pending"
    assert payload["approval_id"] == "appr-1"
    assert payload["event_id"] == env.event_id
    assert payload["tool_name"] == "stock_price"
    assert payload["destination"] == "auditor@example.com"
    # Nunca se filtra el secreto crudo.
    assert "s3cr3t" not in str(payload)


def test_notifier_falla_aborta_con_notification_error():
    channel = RecordingChannel(ok=False)
    notifier = ApprovalNotifier(channel)
    env = _envelope()
    with pytest.raises(NotificationError):
        notifier.notify_approval_pending(
            envelope=env,
            approval_id="appr-1",
            idempotency_key="hitl:ev-1",
            expires_at=None,
            destination=None,
            sanitized_preview=env.telemetry_arguments,
        )