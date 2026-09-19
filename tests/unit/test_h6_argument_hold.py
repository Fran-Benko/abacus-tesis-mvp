"""Tests de H6 — Unidad 1: hold de argumentos (InMemoryArgumentHold).

Verifican las garantias del hold idempotente y acotado:
- Idempotencia por (event_id, digest): misma llamada devuelve el mismo hold_id.
- Conflicto si el mismo evento pide un digest distinto (HoldConflict).
- Acotado por tamano de argumento (HoldCapacity si excede max_held_argument_bytes).
- Acotado por total (HoldCapacity si el acumulado excede max_held_total_bytes).
- Vencimiento por TTL y liberacion explicita (`release`).
"""
from datetime import datetime, timedelta, timezone

import pytest

from argentgob.hitl.argument_hold import InMemoryArgumentHold
from argentgob.hitl.errors import HoldCapacityError, HoldConflictError


def _future(**kw):
    return datetime.now(timezone.utc) + timedelta(seconds=60, **kw)


def _hold(settings):
    return InMemoryArgumentHold(settings)


def test_hold_registra_y_recupera_argumentos(settings):
    hold = _hold(settings)
    args = {"query": "  AAPL  ", "max_results": 3}
    hid = hold.hold(
        event_id="ev-1",
        tool_name="stock_price",
        agent_id="agent-1",
        argument=args,
        expires_at=_future(),
    )
    assert isinstance(hid, str) and hid
    got = hold.get("ev-1")
    assert got == args
    # get devuelve una copia, no la referencia interna.
    assert got is not args


def test_hold_idempotente_mismo_digest_devuelve_mismo_id(settings):
    hold = _hold(settings)
    args = {"query": "AAPL"}
    hid1 = hold.hold(
        event_id="ev-1",
        tool_name="t",
        agent_id="a",
        argument=args,
        expires_at=_future(),
    )
    hid2 = hold.hold(
        event_id="ev-1",
        tool_name="t",
        agent_id="a",
        argument=dict(args),  # contenido identico, orden distinto -> mismo digest
        expires_at=_future(),
    )
    assert hid1 == hid2
    assert hold.size == 1


def test_hold_conflicto_digest_distinto(settings):
    hold = _hold(settings)
    hold.hold(
        event_id="ev-1",
        tool_name="t",
        agent_id="a",
        argument={"query": "AAPL"},
        expires_at=_future(),
    )
    with pytest.raises(HoldConflictError):
        hold.hold(
            event_id="ev-1",
            tool_name="t",
            agent_id="a",
            argument={"query": "TSLA"},
            expires_at=_future(),
        )


def test_hold_rechaza_argumento_muy_grande(settings):
    hold = _hold(settings)
    big = {"blob": "x" * (settings.max_held_argument_bytes + 64)}
    with pytest.raises(HoldCapacityError):
        hold.hold(
            event_id="ev-1",
            tool_name="t",
            agent_id="a",
            argument=big,
            expires_at=_future(),
        )
    assert hold.size == 0


def test_hold_total_acotado(settings):
    # Tope total pequeño (pero >= max_held_argument_bytes) para forzar la
    # capacidad acumulada sin violar la invariante de config.
    from argentgob.core.config import Settings

    small = Settings(
        environment="TEST",
        log_level="WARNING",
            max_held_argument_bytes=2048,
            max_held_total_bytes=2500,
    )
    hold = _hold(small)
    first = {"padding": "x" * 1500}
    hold.hold(
        event_id="ev-1",
        tool_name="t",
        agent_id="a",
        argument=first,
        expires_at=_future(),
    )
    second = {"padding": "y" * 1500}
    with pytest.raises(HoldCapacityError):
        hold.hold(
            event_id="ev-2",
            tool_name="t",
            agent_id="a",
            argument=second,
            expires_at=_future(),
        )


def test_hold_expira_y_evicta(settings):
    hold = _hold(settings)
    hold.hold(
        event_id="ev-exp",
        tool_name="t",
        agent_id="a",
        argument={"query": "AAPL"},
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    # get() purga vencidos internamente al consultar.
    assert hold.get("ev-exp") is None
    assert hold.size == 0


def test_hold_release_libera_bytes(settings):
    hold = _hold(settings)
    hold.hold(
        event_id="ev-1",
        tool_name="t",
        agent_id="a",
        argument={"query": "AAPL"},
        expires_at=_future(),
    )
    assert hold.total_bytes > 0
    hold.release("ev-1")
    assert hold.get("ev-1") is None
    assert hold.total_bytes == 0
    assert hold.size == 0