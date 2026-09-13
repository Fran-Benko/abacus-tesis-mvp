"""
Tests del ToolCallEnvelope.

Verifican la construcción del envelope, el cálculo del digest SHA-256, el tamaño
de payload y la separación entre execution_arguments y telemetry_arguments
(INV-04/INV-05).
"""
import json

from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope


def _build(args: dict) -> ToolCallEnvelope:
    return ToolCallEnvelope.build(
        agent=AgentIdentity(id="a-1", role="analyst"),
        tool_name="stock_price",
        operation_class="READ",
        resource="yahoo_finance",
        environment="TEST",
        execution_arguments=args,
    )


def test_build_genera_digest_sha256():
    """El digest debe tener el prefijo 'sha256:' y 64 hex chars."""
    env = _build({"ticker": "AAPL"})
    assert env.payload_digest.startswith("sha256:")
    hex_part = env.payload_digest.split(":", 1)[1]
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_digest_cambia_si_cambian_los_argumentos():
    """Argumentos distintos producen digests distintos."""
    env1 = _build({"ticker": "AAPL"})
    env2 = _build({"ticker": "MSFT"})
    assert env1.payload_digest != env2.payload_digest


def test_digest_es_determinista():
    """Los mismos argumentos producen siempre el mismo digest (orden de claves estable)."""
    env1 = _build({"ticker": "AAPL", "range": "1d"})
    env2 = _build({"range": "1d", "ticker": "AAPL"})
    assert env1.payload_digest == env2.payload_digest


def test_payload_size_refleja_tamano_json_real():
    """El tamaño de payload debe coincidir con el JSON serializado real."""
    args = {"query": "precio de AAPL"}
    env = _build(args)
    esperado = len(json.dumps(args, ensure_ascii=False).encode())
    assert env.payload_size_bytes == esperado


def test_telemetry_arguments_empieza_vacio():
    """telemetry_arguments se llena en el Módulo B, no en build (INV-04)."""
    env = _build({"ticker": "AAPL"})
    assert env.telemetry_arguments == {}
    assert env.execution_arguments == {"ticker": "AAPL"}


def test_envelope_tiene_identificadores_unicos():
    """Cada envelope recibe un event_id y session_id únicos."""
    env1 = _build({"ticker": "AAPL"})
    env2 = _build({"ticker": "AAPL"})
    assert env1.event_id != env2.event_id
    assert env1.session_id != env2.session_id
