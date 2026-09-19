"""Tests de H6 — Unidad 4: resume con ExecutionCapability de un solo uso.

Cubren el flujo publico `GovernedResumeService.resume(token_id, presented_token)`:
- Resume exitoso emitir capacidades (ExecutionCapability) y consume el token.
- Token invalido (hash no coincide) -> TokenConsumError.
- Token reusado / ya REDEEMED -> TokenConsumError.
- Approval no APPROVED -> TokenConsumError.
- Sin hold conservado -> ArgumentsUnavailableError (no consume ni ejecuta).
"""
from datetime import datetime, timedelta, timezone

import pytest

from argentgob.core.config import Settings
from argentgob.hitl.argument_hold import InMemoryArgumentHold
from argentgob.hitl.errors import ArgumentsUnavailableError, TokenConsumError
from argentgob.hitl.governed_resume import GovernedResumeService
from argentgob.hitl.tokens import hash_token
from tests.unit._h6_fakes import FakeConn


def _settings():
    return Settings(
        environment="TEST",
        log_level="WARNING",
        max_payload_bytes=65536,
        max_held_argument_bytes=32768,
        max_held_total_bytes=262144,
        approval_ttl_seconds=300,
    )


def _token_row(token_id="tok-1", approval_id="appr-1", state="ACTIVE", presented="secret"):
    return (
        token_id,
        approval_id,
        hash_token(presented),
        state,
        None,   # created_at
        None,   # expires_at
        None,   # redeemed_at
    )


def _approval_row(approval_id="appr-1", event_id="ev-1", state="APPROVED", digest="dig-abc"):
    return (
        approval_id, event_id, "dec-1", "agent-1", "stock_price",
        digest, "hitl:ev-1", state, None, None, None, None, "APPROVED",
    )


def _svc(route, hold=None, settings=None):
    return GovernedResumeService(
        settings=settings or _settings(),
        hold=hold,
        connection_factory=lambda: FakeConn(route),
    )


def _happy_route(presented="secret"):
    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row(presented=presented))
        if "FROM approval_requests" in sql:
            return (0, _approval_row())
        if "UPDATE approval_resume_tokens" in sql:
            return (1, None)
        if "UPDATE approval_requests" in sql:
            return (1, None)
        return (0, None)

    return route


def test_resume_ok_emite_capability_consume_token():
    from argentgob.hitl.argument_hold import _canonical_bytes, sha256_hex

    presented = "secret-token-plain"
    args = {"query": "AAPL"}
    argument_digest = sha256_hex(_canonical_bytes(args))  # digest derivado real

    hold = InMemoryArgumentHold(_settings())
    hold.hold(
        event_id="ev-1",
        tool_name="stock_price",
        agent_id="agent-1",
        argument=args,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
    )

    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row(presented=presented))
        if "FROM approval_requests" in sql:
            row = _approval_row(state="APPROVED", digest=argument_digest)
            return (0, row)
        if "UPDATE approval_resume_tokens" in sql:
            return (1, None)
        if "UPDATE approval_requests" in sql:
            return (1, None)
        return (0, None)

    svc = GovernedResumeService(
        settings=_settings(),
        hold=hold,
        connection_factory=lambda: FakeConn(route),
    )
    cap = svc.resume(token_id="tok-1", presented_token=presented)
    assert cap is not None
    assert cap.tool_name == "stock_price"
    assert cap.consumed is False  # se consume recién por el ExecutionOrchestrator
    assert cap.event_id == "ev-1"
    assert cap.argument_digest == argument_digest
    assert cap.expires_at is not None


def test_resume_token_invalido_hash():
    presented = "secret"
    wrong = "other"

    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row(presented=presented))
        return (0, None)

    svc = _svc(route, hold=InMemoryArgumentHold(_settings()))
    with pytest.raises(TokenConsumError):
        svc.resume(token_id="tok-1", presented_token=wrong)


def test_resume_token_reusado_no_activo():
    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row(state="REDEEMED"))
        return (0, None)

    svc = _svc(route, hold=InMemoryArgumentHold(_settings()))
    with pytest.raises(TokenConsumError):
        svc.resume(token_id="tok-1", presented_token="secret")


def test_resume_approval_no_aprobada():
    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row())
        if "FROM approval_requests" in sql:
            return (0, _approval_row(state="PENDING"))
        return (0, None)

    svc = _svc(route, hold=InMemoryArgumentHold(_settings()))
    with pytest.raises(TokenConsumError):
        svc.resume(token_id="tok-1", presented_token="secret")


def test_resume_sin_hold_no_consume():
    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row())
        if "FROM approval_requests" in sql:
            return (0, _approval_row())
        return (0, None)

    svc = _svc(route, hold=None)  # sin hold conservado (reinicio real)
    with pytest.raises(ArgumentsUnavailableError):
        svc.resume(token_id="tok-1", presented_token="secret")


def test_resume_digest_mismatch_no_consume():
    presented = "secret"
    from argentgob.hitl.argument_hold import _canonical_bytes, sha256_hex

    args = {"query": "AAPL"}
    hold_args_digest = sha256_hex(_canonical_bytes(args))
    hold = InMemoryArgumentHold(_settings())
    hold.hold(
        event_id="ev-1",
        tool_name="stock_price",
        agent_id="agent-1",
        argument=args,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
    )

    def route(sql, params=None):
        if "FROM approval_resume_tokens" in sql:
            return (0, _token_row(presented=presented))
        if "FROM approval_requests" in sql:
            return (0, _approval_row(state="APPROVED", digest="dig-DIFFERENT"))
        return (0, None)

    svc = _svc(route, hold=hold)
    with pytest.raises(ArgumentsUnavailableError):
        svc.resume(token_id="tok-1", presented_token=presented)