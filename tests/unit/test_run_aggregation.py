"""Tests de la regla de agregación de estado del run (AV-01, contrato §4).

`SUCCEEDED` solo si la tarea requerida tiene salida válida y ninguna acción
necesaria quedó retenida o fallida; un informe con acciones retenidas es
`PARTIAL`; sin ninguna ejecución autorizada por gobernanza es `BLOCKED`.
"""
from __future__ import annotations

import pytest

from argentgob.live.runner import _DecisionTally


def test_todas_las_tentativas_pass_con_resultado_es_succeeded():
    tally = _DecisionTally()
    tally.record("PASS")
    tally.record("PASS")
    assert tally.final_state(has_result=True) == "SUCCEEDED"


def test_retencion_hitl_con_resultado_es_partial():
    """Una acción necesaria retenida impide SUCCEEDED (contrato §4)."""
    tally = _DecisionTally()
    tally.record("PASS")
    tally.record("HITL")
    assert tally.final_state(has_result=True) == "PARTIAL"


def test_bloqueo_con_resultado_es_partial():
    tally = _DecisionTally()
    tally.record("PASS")
    tally.record("BLOCK")
    assert tally.final_state(has_result=True) == "PARTIAL"


def test_sin_ejecucion_autorizada_es_blocked():
    """Si ninguna operación requerida pudo ejecutarse, el run es BLOCKED."""
    tally = _DecisionTally()
    tally.record("HITL")
    tally.record("HITL")
    assert tally.final_state(has_result=True) == "BLOCKED"


def test_abortado_sin_resultado_es_blocked():
    tally = _DecisionTally()
    tally.record("BLOCK")
    assert tally.final_state(has_result=False, aborted=True) == "BLOCKED"


def test_sin_decisiones_y_sin_resultado_no_es_succeeded():
    tally = _DecisionTally()
    assert tally.final_state(has_result=False) == "PARTIAL"


@pytest.mark.parametrize("action", ["PASS", "HITL", "BLOCK", None, "DESCONOCIDO"])
def test_acciones_desconocidas_no_rompen_el_conteo(action):
    tally = _DecisionTally()
    tally.record(action)
    assert tally.final_state(has_result=True) in ("SUCCEEDED", "PARTIAL", "BLOCKED")
