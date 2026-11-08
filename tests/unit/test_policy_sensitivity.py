"""Tests del chequeo de `sensitivity_limit` en el evaluador (hito prioridad media).

Una política declarativa puede declarar `sensitivity_limit` (LOW/MEDIUM/HIGH).
Si la predicción del modelo (`envelope.prediction.predicted_risk_class`) supera
ese límite, el evaluador decide BLOCK con `SENSITIVITY_EXCEEDED` (más específico
que el PREDICTED_HIGH_RISK genérico del pipeline).
"""
from __future__ import annotations

from unittest.mock import patch

from argentgob.core.envelope import ToolCallEnvelope
from argentgob.core.errors import GovernanceAction, Obligation, ReasonCode
from argentgob.module_c.deterministic_evaluator import DeterministicEvaluator
from tests.unit.test_h5_policy_engine import _envelope, _policy, _store_with


def _envelope_with_prediction(predicted_class: str) -> ToolCallEnvelope:
    env = _envelope(tool_name="news", operation_class="EXTERNAL_SEND")
    # RiskPrediction mínima: solo importa predicted_risk_class aquí.
    env.prediction = type(
        "Pred",
        (),
        {"predicted_risk_class": predicted_class},
    )()
    return env


def test_sensibilidad_excedida_bloquea_con_reason_especifico():
    policies = [
        _policy(
            policy_id="POL-lim",
            tool_name="news",
            operation_class="EXTERNAL_SEND",
            sensitivity_limit="MEDIUM",
            obligations=[],
        )
    ]
    store = _store_with([])
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope_with_prediction("HIGH"))
    assert dec.action == GovernanceAction.BLOCK
    assert ReasonCode.SENSITIVITY_EXCEEDED in dec.reason_codes


def test_sensibilidad_dentro_del_limite_passa():
    policies = [
        _policy(
            policy_id="POL-lim",
            tool_name="news",
            operation_class="EXTERNAL_SEND",
            sensitivity_limit="MEDIUM",
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        )
    ]
    store = _store_with([])
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope_with_prediction("LOW"))
    assert dec.action == GovernanceAction.PASS


def test_sin_prediccion_no_aplica_limite():
    policies = [
        _policy(
            policy_id="POL-lim",
            tool_name="news",
            operation_class="EXTERNAL_SEND",
            sensitivity_limit="LOW",
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        )
    ]
    store = _store_with([])
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(
            _envelope(tool_name="news", operation_class="EXTERNAL_SEND")
        )  # sin prediction
    assert dec.action == GovernanceAction.PASS


def test_sin_sensitivity_limit_no_afecta_decison():
    # Política SIN sensitivity_limit: la predicción HIGH no la bloquea.
    policies = [
        _policy(
            policy_id="POL-sin-limite",
            tool_name="news",
            operation_class="EXTERNAL_SEND",
            sensitivity_limit=None,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        )
    ]
    store = _store_with([])
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope_with_prediction("HIGH"))
    assert dec.action == GovernanceAction.PASS  # límite None -> no excede


def test_risk_rank_compara_correctamente():
    rank = DeterministicEvaluator._risk_rank
    assert rank("LOW") < rank("MEDIUM") < rank("HIGH")
    assert rank("HIGH") == 3
    assert rank("desconocido") == 0
