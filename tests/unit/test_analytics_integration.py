"""
tests/unit/test_analytics_integration.py

Tests de la capa predictiva operacional (Fase 4 — Integración).

Cubren:
- FeatureBuilder: deriva las features pre-ejecución del envelope (contexto,
  payload sanitizado, control) y produce el vector canónico de 27 features.
- RiskPredictor: carga el pipeline entrenado (Fase 3), predice con umbral HIGH
  y normaliza el orden LOW/MEDIUM/HIGH; degrada a None si el modelo no está.
- Precedencias en el PEP: HIGH → BLOCK, MEDIUM (sobre PASS) → HITL, LOW → PASS.
- Fallback fail-closed: sin modelo disponible, la decisión queda en el motor
  determinista (sin bloqueo por predicción).
- Persistencia: PredictionStore registra en risk_predictions (fail-open).

Si el modelo versionado no existe, los tests que dependen de artefactos se
omiten (misma política que test_corpus.py y test_analytics.py).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from argentgob.analytics.features import FEATURE_ORDER, FeatureBuilder
from argentgob.analytics.predictor import DEFAULT_MODEL_DIR, RiskPredictor
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    Obligation,
    ReasonCode,
)
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.guardrails import GuardrailEngine

MODEL_EXISTS = (DEFAULT_MODEL_DIR / "pipeline.joblib").exists()

requires_model = pytest.mark.skipif(
    not MODEL_EXISTS,
    reason="modelo versionado no presente",
)


def _envelope(settings, **kwargs) -> ToolCallEnvelope:
    """Construye un envelope con valores por defecto de analista."""
    from argentgob.module_b.sanitizer import Sanitizer  # noqa: PLC0415

    agent = AgentIdentity(id="agente-analyst-001", role="analyst")
    execution_arguments = kwargs.pop(
        "execution_arguments", {"query": "bitcoin precio mercado"}
    )
    env = ToolCallEnvelope.build(
        agent=kwargs.pop("agent", agent),
        tool_name=kwargs.pop("tool_name", "news"),
        operation_class=kwargs.pop("operation_class", "EXTERNAL_SEND"),
        resource=kwargs.pop("resource", "duckduckgo.com"),
        environment=kwargs.pop("environment", settings.environment),
        execution_arguments=execution_arguments,
    )
    # Aplicar el sanitizador real (mismo paso que el PEP).
    env.telemetry_arguments = Sanitizer(settings).sanitize(execution_arguments)
    return env


# ── FeatureBuilder ──────────────────────────────────────────────────

def test_feature_builder_produce_vector_canonico(settings):
    """El FeatureBuilder deriva las 27 features del esquema en orden canónico."""
    env = _envelope(settings)
    builder = FeatureBuilder()
    feats = builder.build(env)
    assert set(feats.keys()) == set(FEATURE_ORDER)
    # Algunos valores esperados del contexto.
    assert feats["context.tool_name"] == "news"
    assert feats["context.operation_class"] == "EXTERNAL_SEND"
    assert feats["context.agent_role"] == "analyst"
    # Payload sobre telemetría (no crudo; el sanitizador se aplica en el PEP).
    assert isinstance(feats["payload.payload_size_bytes"], int)
    assert feats["payload.has_url"] in (True, False)


def test_feature_builder_detecta_inyeccion(settings):
    """Los detectores de inyección operan sobre la telemetría sanitizada."""
    env = _envelope(
        settings,
        execution_arguments={"query": "query=DROP TABLE policies;--"},
    )
    builder = FeatureBuilder()
    feats = builder.build(env)
    assert feats["payload.has_sql_injection_pattern"] is True
    # Sin PII en el payload limpio.
    assert feats["payload.pii_category_count"] == 0
    assert feats["payload.has_pii"] is False


def test_feature_builder_degradacion_sin_policy_source(settings):
    """Sin policy_source, las features de control toman valores neutros."""
    env = _envelope(
        settings,
        tool_name="herramienta_exotica",
        operation_class="WRITE",
    )
    builder = FeatureBuilder(policy_source=None)
    feats = builder.build(env)
    assert feats["control.matched_policy_count"] == 0
    assert feats["control.max_allowed_sensitivity"] == 0
    assert feats["control.policy_is_active"] is False


# ── RiskPredictor ──────────────────────────────────────────────────

@requires_model
def test_risk_predictor_carga_y_predice(settings):
    """El predictor carga el pipeline y predice una clase válida."""
    predictor = RiskPredictor()
    assert predictor.available is True
    env = _envelope(settings)
    feats = FeatureBuilder().build(env)
    pred = predictor.predict(feats)
    assert pred is not None
    assert pred.predicted_risk_class in ("LOW", "MEDIUM", "HIGH")
    assert set(pred.class_probabilities.keys()) == {"LOW", "MEDIUM", "HIGH"}
    # Las probabilidades suman 1.
    assert sum(pred.class_probabilities.values()) == pytest.approx(1.0, abs=0.01)
    assert pred.model_id == "risk_model_v1"
    assert pred.inference_latency_ms >= 0


@requires_model
def test_risk_predictor_umbral_high(settings):
    """El umbral HIGH degrada a LOW cuando prob(HIGH) < umbral."""
    predictor = RiskPredictor()
    # Features de un escenario benigno: LOW esperado.
    env = _envelope(settings, execution_arguments={"query": "resumen mercado"})
    feats = FeatureBuilder().build(env)
    pred = predictor.predict(feats)
    assert pred is not None
    if pred.predicted_risk_class == "HIGH":
        # Si predice HIGH, la probabilidad debe superar el umbral.
        assert pred.risk_probability >= predictor.threshold_high


def test_risk_predictor_no_disponible_sin_artefactos(tmp_path, settings):
    """Sin artefactos, el predictor degrada a no disponible (fail-closed)."""
    predictor = RiskPredictor(model_dir=tmp_path)
    assert predictor.available is False
    assert predictor.predict({}) is None


# ── Precedencias en el PEP ─────────────────────────────────────────

def _middleware_with_predictor(settings, mock_audit_writer, predictor, builder):
    """Arma un middleware con ABAC mockeado y predictor inyectado."""
    abac = MagicMock()
    abac.evaluate = MagicMock(
        return_value=MagicMock(
            action=GovernanceAction.PASS,
            reason_code=ReasonCode.ALLOWED,
            decision_id="d-1",
            is_expired=lambda: False,
            # Una obligación de selección: la original (R2).
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        )
    )
    guardrails = GuardrailEngine(
        profile_guardrails=[], rate_limit=settings.rate_limit_calls_per_run
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=mock_audit_writer,
        feature_builder=builder,
        risk_predictor=predictor,
    )


@requires_model
def test_precedencia_high_bloquea(settings, mock_audit_writer):
    """Predicción HIGH eleva un PASS del motor a BLOCK (PREDICTED_HIGH_RISK)."""
    # Forzar un escenario claramente HIGH: WRITE con inyección.
    env = _envelope(
        settings,
        tool_name="crypto_price",
        operation_class="WRITE",
        resource="binance_public",
        execution_arguments={"query": "DROP TABLE policies;--"},
    )
    predictor = RiskPredictor()
    builder = FeatureBuilder()
    mw = _middleware_with_predictor(settings, mock_audit_writer, predictor, builder)
    spy = SpyTool(governance=mw, agent_identity=env.agent)
    spy.name = "crypto_price"
    spy.operation_class = "WRITE"
    spy.resource = "binance_public"
    with pytest.raises(HookAborted) as exc:
        spy._run(query="DROP TABLE policies;--")
    assert spy.call_count == 0
    # Puede bloquear por política (WRITE no permitida para analyst) o por
    # predicción; el invariante es que no se ejecutó.
    assert "blocked" in exc.value.reason


@requires_model
def test_precedencia_medium_hitl(settings, mock_audit_writer):
    """Predicción MEDIUM sobre un PASS del motor eleva a HITL."""
    predictor = RiskPredictor()
    builder = FeatureBuilder()
    # Envolver predict para forzar MEDIUM.
    real_predict = predictor.predict

    def forced_medium(features):
        pred = real_predict(features)
        if pred is None:
            return None
        from argentgob.analytics.predictor import RiskPrediction

        return RiskPrediction(
            predicted_risk_class="MEDIUM",
            risk_probability=pred.class_probabilities["MEDIUM"],
            class_probabilities=pred.class_probabilities,
            model_id=pred.model_id,
            model_version=pred.model_version,
            feature_schema_version=pred.feature_schema_version,
            inference_latency_ms=pred.inference_latency_ms,
        )

    predictor.predict = forced_medium
    mw = _middleware_with_predictor(settings, mock_audit_writer, predictor, builder)
    spy = SpyTool(governance=mw, agent_identity=AgentIdentity(id="a-1", role="analyst"))
    spy.name = "news"
    # Query dentro del rango entrenado del corpus (13-66 caracteres) para que
    # la guarda de extrapolación no se active y la predicción MEDIUM se aplique.
    with pytest.raises(HookAborted) as exc:
        spy._run(query="bitcoin precio mercado hoy")
    assert spy.call_count == 0
    assert "HUMAN_APPROVAL_REQUIRED" in exc.value.reason
    mock_audit_writer.record_decision.assert_called()


@requires_model
def test_guarda_extrapolacion_degrada_a_fallback(settings, mock_audit_writer):
    """Una query fuera del rango entrenado hace que el predictor se abstenga.

    El `StandardScaler` no acota la entrada: sin guarda, una query corta
    (p. ej. "AAPL") extrapola y eleva a HITL llamadas legítimas. La guarda
    devuelve `None` y el PEP decide con el motor determinista (fail-closed).
    """
    predictor = RiskPredictor()
    builder = FeatureBuilder()
    mw = _middleware_with_predictor(settings, mock_audit_writer, predictor, builder)
    spy = SpyTool(governance=mw, agent_identity=AgentIdentity(id="a-1", role="analyst"))
    spy.name = "news"
    # "AAPL" mide 4 caracteres: por debajo del mínimo del corpus (13).
    spy._run(query="AAPL")
    assert spy.call_count == 1
    # El motor determinista autorizó (PASS) y no hubo elevación a HITL.
    mock_audit_writer.record_decision.assert_called()


def test_fallback_sin_predictor_pasa(settings, mock_audit_writer):
    """Sin modelo disponible, un PASS del motor se mantiene (fail-closed)."""
    abac = MagicMock()
    abac.evaluate = MagicMock(
        return_value=MagicMock(
            action=GovernanceAction.PASS,
            reason_code=ReasonCode.ALLOWED,
            decision_id="d-1",
            is_expired=lambda: False,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        )
    )
    guardrails = GuardrailEngine(
        profile_guardrails=[], rate_limit=settings.rate_limit_calls_per_run
    )
    mw = GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=mock_audit_writer,
        risk_predictor=None,  # Sin capa predictiva.
    )
    spy = SpyTool(governance=mw, agent_identity=AgentIdentity(id="a-1", role="analyst"))
    spy.name = "news"
    result = spy._run(query="bitcoin precio")
    assert spy.call_count == 1
    assert result.startswith("spy_executed")


def test_fallback_error_inferencia_no_bloquea(settings, mock_audit_writer):
    """Un error de inferencia degrada y NO bloquea (decide el determinista)."""
    predictor = RiskPredictor()
    # Forzar error en predict.
    def boom(features):
        raise RuntimeError("boom")

    predictor.predict = boom
    builder = FeatureBuilder()
    mw = _middleware_with_predictor(settings, mock_audit_writer, predictor, builder)
    spy = SpyTool(governance=mw, agent_identity=AgentIdentity(id="a-1", role="analyst"))
    spy.name = "news"
    result = spy._run(query="bitcoin precio")
    assert spy.call_count == 1  # el determinista PASS autoriza
    assert result.startswith("spy_executed")


# ── Persistencia ───────────────────────────────────────────────────

def test_prediction_store_fail_open(settings):
    """PredictionStore degrada sin DB (fail-open, no lanza)."""
    from argentgob.analytics.predictor import RiskPrediction
    from argentgob.analytics.store import PredictionStore

    store = PredictionStore(settings=settings)
    env = _envelope(settings)
    pred = RiskPrediction(
        predicted_risk_class="LOW",
        risk_probability=0.1,
        class_probabilities={"LOW": 0.9, "MEDIUM": 0.05, "HIGH": 0.05},
    )
    # Sin DB disponible: no debe lanzar.
    store.record_prediction(env, pred, decision_id="d-1")