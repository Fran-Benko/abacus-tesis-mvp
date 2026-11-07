"""
PredictionStore: persiste predicciones de riesgo en PostgreSQL.

Fase 4 — Integración operacional. Sigue el patrón de AuditWriter: fail-open
para la observabilidad (si la DB no está disponible, se registra el error pero
no se propaga la excepción). El enforcement NO debe fallar por falta de
persistencia de la predicción.

INV-05: solo se persisten metadatos y probabilidades, nunca el payload crudo.
"""
from __future__ import annotations

import json

from argentgob.analytics.predictor import RiskPrediction
from argentgob.core.config import Settings
from argentgob.core.envelope import ToolCallEnvelope
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class PredictionStore:
    """Escribe predicciones de riesgo; degrada de forma segura sin DB."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def record_prediction(
        self,
        envelope: ToolCallEnvelope,
        prediction: RiskPrediction,
        decision_id: str | None = None,
    ) -> None:
        """Inserta una predicción en la tabla risk_predictions."""
        try:
            from argentgob.db.connection import get_connection

            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO risk_predictions (
                            prediction_id, event_id, decision_id, model_id,
                            model_version, predicted_risk_class, risk_probability,
                            class_probabilities, inference_latency_ms,
                            feature_schema_version, predicted_at
                        ) VALUES (
                            %(prediction_id)s, %(event_id)s, %(decision_id)s,
                            %(model_id)s, %(model_version)s,
                            %(predicted_risk_class)s, %(risk_probability)s,
                            %(class_probabilities)s, %(inference_latency_ms)s,
                            %(feature_schema_version)s, now()
                        )
                        ON CONFLICT (prediction_id) DO UPDATE
                            SET decision_id = COALESCE(
                                EXCLUDED.decision_id, risk_predictions.decision_id
                            )
                        """,
                        {
                            "prediction_id": f"pred-{envelope.event_id}",
                            "event_id": envelope.event_id,
                            "decision_id": decision_id,
                            "model_id": prediction.model_id,
                            "model_version": prediction.model_version,
                            "predicted_risk_class": prediction.predicted_risk_class,
                            "risk_probability": prediction.risk_probability,
                            "class_probabilities": json.dumps(
                                prediction.class_probabilities
                            ),
                            "inference_latency_ms": prediction.inference_latency_ms,
                            "feature_schema_version": prediction.feature_schema_version,
                        },
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - fail-open de observabilidad
            log.warning(
                "prediction_persist_failed",
                detail=str(exc),
                event_id=envelope.event_id,
            )
