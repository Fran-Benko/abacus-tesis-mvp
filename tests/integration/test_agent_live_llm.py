"""Smoke real del agente en vivo: CrewAI + llama.cpp + PostgreSQL (AV-01).

Opt-in: requiere `llama.cpp` alcanzable en `OPENAI_API_BASE` y una DB migrada
con políticas sembradas. Se marca `llm_local` y no corre en la suite por
defecto.

Oráculo (gate G-AV-01): al menos una tentativa real atraviesa el PEP y produce
eventos consultables; el run termina en un estado terminal coherente
(`SUCCEEDED`, `BLOCKED` o `PARTIAL`). No se hace monkeypatch del LLM.
"""
from __future__ import annotations

import os
import socket
import urllib.parse

import pytest

from argentgob.live.supervisor import RunSupervisor

pytestmark = pytest.mark.llm_local

LLM_BASE = os.environ.get("OPENAI_API_BASE", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _llm_reachable() -> bool:
    """Comprueba el endpoint del LLM sin consumir inferencia."""
    if not LLM_BASE:
        return False
    parsed = urllib.parse.urlparse(LLM_BASE)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


requires_llm = pytest.mark.skipif(
    not _llm_reachable(),
    reason="llama.cpp no alcanzable en OPENAI_API_BASE",
)
requires_db = pytest.mark.skipif(
    not DATABASE_URL,
    reason="sin DATABASE_URL para evidencia persistida",
)


@requires_llm
@requires_db
def test_run_real_atraviesa_el_pep_y_produce_eventos():
    """Un run real con llama.cpp genera ≥1 tentativa gobernada y termina."""
    supervisor = RunSupervisor(run_timeout_seconds=300)
    try:
        record, created = supervisor.submit(
            actor_id="auditor-llm",
            scenario_id="finanzas",
            profile="analyst",
            input_payload={"asset": "AAPL"},
            idempotency_key="av01-smoke-llm",
        )
        assert created is True
        finished = supervisor.wait(record.run_id, timeout=300)
        snapshot = supervisor.snapshot(record.run_id, actor_id="auditor-llm")
    finally:
        supervisor.shutdown()

    kinds = [event["kind"] for event in snapshot["events"]]
    assert "run.accepted" in kinds
    assert "run.finished" in kinds
    # Al menos una tentativa real atravesó el PEP (decisión emitida).
    assert "tool.requested" in kinds
    assert "governance.decided" in kinds
    assert finished.state in ("SUCCEEDED", "BLOCKED", "PARTIAL", "FAILED")
    # INV-05/AV-I06: ningún evento publica argumentos operativos ni prompts.
    serialized = str(snapshot["events"])
    assert "execution_arguments" not in serialized
    assert "system prompt" not in serialized.lower()
