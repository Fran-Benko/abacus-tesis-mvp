"""Tests de la demo end-to-end (scripts/demo_lib.py).

Verifican que cada escenario de la demo produce el outcome esperado sobre los
componentes reales (middleware + policy engine) en modo memoria, y que el
flujo HITL completo termina con ejecución gobernada exitosa.

Se ejecutan en memoria (sin PostgreSQL, sin red, sin LLM) — mismo patrón que
`tests/integration/test_hitl_end_to_end.py` (sustituye get_connection por la
conexión en memoria de `_mem_db.py`).

El wiring se construye una vez por test; `restore()` devuelve el
`get_connection` global a su estado original (importante en suite runs).
"""
from __future__ import annotations

import os
import sys

# Permite importar scripts.demo_lib desde la raíz del repo (pytest no añade
# la raíz al path cuando los tests están en tests/unit/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from argentgob.core.config import get_settings
from argentgob.core.envelope import AgentIdentity
from scripts.demo_lib import (
    DemoStore,
    EscenarioBenigna,
    EscenarioBloqueoPrediccion,
    EscenarioGuardrail,
    EscenarioHitl,
    EscenarioSensibilidad,
    _wiring,
)


@pytest.fixture()
def w():
    """Wiring real en memoria (restaura get_connection al terminar)."""
    settings = get_settings()
    store = DemoStore(real=False)
    wiring = _wiring(store, settings)
    wiring["_store"] = store
    yield wiring
    store.restore()


def _identidad() -> AgentIdentity:
    return AgentIdentity(id="agente-demo-test", role="analyst")


def test_escenario_benigna_ejecuta(w):
    esc = EscenarioBenigna("", "")
    esc.ejecutar(w, _identidad())
    assert esc.outcome.startswith("OK:")
    assert esc.detalle["call_count"] == 1


def test_escenario_bloqueo_prediccion_no_ejecuta(w):
    esc = EscenarioBloqueoPrediccion("", "")
    esc.ejecutar(w, _identidad())
    assert esc.outcome.startswith("BLOQUEADO: blocked:PREDICTED_HIGH_RISK")
    assert esc.detalle["call_count"] == 0


def test_escenario_guardrail_detecta_inyeccion(w):
    esc = EscenarioGuardrail("", "")
    esc.ejecutar(w, _identidad())
    assert "INJECTION_PATTERN_DETECTED" in esc.outcome
    assert esc.detalle["call_count"] == 0


def test_escenario_sensibilidad_bloquea_politica(w):
    esc = EscenarioSensibilidad("", "")
    esc.ejecutar(w, _identidad())
    assert "SENSITIVITY_EXCEEDED" in esc.outcome
    assert esc.detalle["reason"] == "SENSITIVITY_EXCEEDED"


def test_escenario_hitl_completo(w):
    esc = EscenarioHitl("", "")
    esc.ejecutar(w, _identidad())
    assert esc.detalle["resolucion"] == "approved"
    assert esc.detalle["ejecuciones"] == 1
    assert esc.detalle["capability_consumed_final"] is True
    assert "governed:SUCCESS" in esc.detalle["ejecucion"]
    assert "HITL COMPLETO" in esc.outcome


def test_demo_completa_sin_errores():
    """Smoke test: los 5 escenarios corren en memoria sin excepciones."""
    from scripts.demo_lib import ejecutar_demo

    escenarios = ejecutar_demo(real=False)
    assert len(escenarios) == 5
    for esc in escenarios:
        assert esc.outcome, f"escenario sin outcome: {esc.titulo}"


def test_record_decision_persiste_policy_decisions(w):
    """El Auditor persiste la decisión en `policy_decisions` (round-trip H5).

    Regresión del bug que la demo --real expuso: `approval_requests` y
    `execution_results` tienen FK a `policy_decisions.event_id`, pero ningún
    componente insertaba en esa tabla, causando FK violations en PostgreSQL
    real. `AuditWriter.record_decision` debe escribir en ambas tablas.
    """
    esc = EscenarioBloqueoPrediccion("", "")
    esc.ejecutar(w, _identidad())
    # La decisión BLOCK se persistió en ambas tablas.
    assert w["_store"].store["governance_decisions"], (
        "deberia persistirse una decision"
    )
    assert w["_store"].store["policy_decisions"], (
        "record_decision debe persistir en policy_decisions (round-trip H5)"
    )
    # La approval del flujo HITL debe poder referenciar policy_decisions.
    hitl = EscenarioHitl("", "")
    hitl.ejecutar(w, _identidad())
    assert "governed:SUCCESS" in hitl.detalle["ejecucion"]
