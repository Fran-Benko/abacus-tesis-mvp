"""Tests de H7 — Unidad 1: payload canónico y encadenamiento SHA-256.

Cubren `canonical_payload`, `canonical_json`, `chain_hash` y
`recompute_chain_hash`:

- La serialización canónica es estable ante distinto orden de claves.
- El encadenamiento es determinístico y sensible a cualquier campo.
- El recompute normaliza payloads equivalentes y detecta cambios de valor.
- El hash cabe en String(80) y el génesis es el valor esperado.
"""
from datetime import datetime, timezone

from argentgob.audit.canonical import (
    GENESIS_HASH,
    canonical_json,
    canonical_payload,
    chain_hash,
    recompute_chain_hash,
)


def test_genesis_hash_formato():
    assert GENESIS_HASH == "sha256:" + "0" * 64
    assert len(GENESIS_HASH) == 71


def test_canonical_json_estable_ante_orden_de_claves():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b
    assert a == '{"a":2,"b":1}'


def test_canonical_payload_incluye_solo_campos_normativos():
    payload = canonical_payload(
        event_type="tool_call",
        entity="stock_price",
        digest="abc123",
        outcome="EXECUTED",
        actor="agent-1",
        decision="PASS",
        approval=None,
    )
    assert '"event_type":"tool_call"' in payload
    assert '"entity":"stock_price"' in payload
    assert '"digest":"abc123"' in payload
    assert '"outcome":"EXECUTED"' in payload
    assert '"actor":"agent-1"' in payload
    assert '"decision":"PASS"' in payload
    assert '"approval":null' in payload
    assert '"timestamp"' in payload


def test_canonical_payload_normaliza_timestamp_a_utc():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = naive.replace(tzinfo=timezone.utc)
    p1 = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="a",
        timestamp=naive,
    )
    p2 = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="a",
        timestamp=aware,
    )
    assert p1 == p2
    assert "+00:00" in p1


def test_chain_hash_deterministico_y_sensible():
    payload = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="a"
    )
    h1 = chain_hash(GENESIS_HASH, payload)
    h2 = chain_hash(GENESIS_HASH, payload)
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == 71

    # Cambiar cualquier campo canónico cambia el hash.
    payload2 = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="b"
    )
    assert chain_hash(GENESIS_HASH, payload2) != h1


def test_chain_hash_encadena_prev():
    p1 = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="a"
    )
    p2 = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="b"
    )
    h1 = chain_hash(GENESIS_HASH, p1)
    h2 = chain_hash(h1, p2)
    # El segundo hash depende del primero (encadenamiento).
    assert h2 != chain_hash(GENESIS_HASH, p2)


def test_recompute_normaliza_payload_equivalente():
    payload = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="a"
    )
    # Payload con distinto orden de claves pero mismo contenido.
    import json

    obj = json.loads(payload)
    shuffled = json.dumps(
        dict(reversed(list(obj.items()))),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert recompute_chain_hash(GENESIS_HASH, shuffled) == chain_hash(
        GENESIS_HASH, payload
    )


def test_recompute_detecta_cambio_de_valor():
    payload = canonical_payload(
        event_type="t", entity="e", digest="d", outcome="o", actor="a"
    )
    tampered = payload.replace('"actor":"a"', '"actor":"x"')
    assert recompute_chain_hash(GENESIS_HASH, tampered) != chain_hash(
        GENESIS_HASH, payload
    )