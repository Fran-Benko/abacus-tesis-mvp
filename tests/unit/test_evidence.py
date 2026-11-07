"""
tests/unit/test_evidence.py

Tests de la evidencia académica (Fase 5).

Cubren:
- `divergence_reason`: clasificación de la relación predicción vs decisión.
- `PredictionQueryService`: consultas parametrizadas sobre `v_risk_evidence`,
  métricas agregadas, divergencias, distribución temporal y versiones.
- `ModelEvidenceService`: métricas reproducibles sobre la partición de
  evaluación congelada (matriz de confusión, métricas por clase, calibración).
- INV-05: las consultas no seleccionan `execution_arguments` ni payload crudo.
"""
from __future__ import annotations

import pytest

from argentgob.analytics.evidence import (
    DEFAULT_CORPUS_PATH,
    DEFAULT_MODEL_DIR,
    ModelEvidenceService,
    PredictionQueryService,
    divergence_reason,
)

MODEL_EXISTS = (DEFAULT_MODEL_DIR / "pipeline.joblib").exists()
CORPUS_EXISTS = DEFAULT_CORPUS_PATH.exists()

requires_artifacts = pytest.mark.skipif(
    not (MODEL_EXISTS and CORPUS_EXISTS),
    reason="artefactos del modelo o corpus no presentes",
)


# ── Divergencia ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("predicted", "final", "expected"),
    [
        ("LOW", "PASS", "CONSISTENTE"),
        ("MEDIUM", "HITL", "CONSISTENTE"),
        ("HIGH", "BLOCK", "CONSISTENTE"),
        ("LOW", "BLOCK", "DETERMINISTA_MAS_ESTRICTO"),
        ("MEDIUM", "BLOCK", "DETERMINISTA_MAS_ESTRICTO"),
        ("HIGH", "PASS", "DETERMINISTA_MENOS_ESTRICTO"),
        ("MEDIUM", "PASS", "DETERMINISTA_MENOS_ESTRICTO"),
        ("LOW", None, "SIN_DECISION"),
        (None, "PASS", "SIN_PREDICCION"),
    ],
)
def test_divergence_reason(predicted, final, expected):
    assert divergence_reason(predicted, final) == expected


# ── PredictionQueryService (conexión en memoria) ────────────────────

class _MemCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.record.append((sql, params))
        self._rows = self.conn.route(sql, params)

    def fetchall(self):
        return self._rows


class _MemConn:
    def __init__(self, route):
        self.route = route
        self.record: list[tuple[str, dict]] = []
        self.closed = False

    def cursor(self, row_factory=None):
        return _MemCursor(self)

    def close(self):
        self.closed = True


def _prediction_row(predicted="HIGH", final="BLOCK", reason="PREDICTED_HIGH_RISK"):
    return {
        "prediction_id": "pred-1",
        "event_id": "ev-1",
        "decision_id": "dec-1",
        "model_id": "risk_model_v1",
        "model_version": "v1",
        "predicted_risk_class": predicted,
        "risk_probability": 0.91,
        "class_probabilities": '{"LOW": 0.02, "MEDIUM": 0.07, "HIGH": 0.91}',
        "inference_latency_ms": 12.5,
        "feature_schema_version": "v1",
        "predicted_at": "2026-11-07T10:00:00+00:00",
        "final_action": final,
        "final_reason_code": reason,
        "tool_name": "news",
        "agent_role": "analyst",
        "operation_class": "EXTERNAL_SEND",
        "decided_at": "2026-11-07T10:00:01+00:00",
    }


def test_list_predictions_agrega_sugerencia_y_divergencia():
    conn = _MemConn(lambda sql, params: [_prediction_row()])
    service = PredictionQueryService(connection_factory=lambda: conn)

    rows = service.list_predictions(limit=10)

    assert len(rows) == 1
    assert rows[0]["suggested_action"] == "BLOCK"
    assert rows[0]["divergence"] == "CONSISTENTE"
    assert conn.closed is True


def test_list_predictions_filtros_parametrizados():
    conn = _MemConn(lambda sql, params: [])
    service = PredictionQueryService(connection_factory=lambda: conn)

    service.list_predictions(predicted_class="HIGH", final_action="BLOCK", limit=5)

    sql, params = conn.record[0]
    assert "predicted_risk_class = %(predicted_class)s" in sql
    assert "final_action = %(final_action)s" in sql
    assert params["predicted_class"] == "HIGH"
    assert params["final_action"] == "BLOCK"
    assert params["limit"] == 5


def test_list_predictions_no_expone_payload_crudo():
    """INV-05: la consulta no selecciona argumentos ni payload crudo."""
    conn = _MemConn(lambda sql, params: [])
    service = PredictionQueryService(connection_factory=lambda: conn)

    service.list_predictions()

    sql, _ = conn.record[0]
    assert "execution_arguments" not in sql
    assert "payload" not in sql.lower()


def test_metrics_calcula_tasa_hitl():
    row = {
        "total": 10,
        "low": 5,
        "medium": 3,
        "high": 2,
        "without_decision": 0,
        "unlinked_decision": 0,
        "hitl_from_prediction": 3,
        "block_from_prediction": 2,
        "avg_probability": 0.42,
        "avg_latency_ms": 11.5,
    }
    conn = _MemConn(lambda sql, params: [row])
    service = PredictionQueryService(connection_factory=lambda: conn)

    metrics = service.metrics()

    assert metrics["total"] == 10
    assert metrics["hitl_from_prediction"] == 3
    assert metrics["hitl_rate"] == 0.3
    assert metrics["avg_latency_ms"] == 11.5


def test_metrics_normaliza_decimal_a_float():
    """Los agregados de PostgreSQL (Decimal) se normalizan a float."""
    from decimal import Decimal

    row = {
        "total": 2,
        "low": 1,
        "medium": 1,
        "high": 0,
        "without_decision": 0,
        "unlinked_decision": 0,
        "hitl_from_prediction": 1,
        "block_from_prediction": 0,
        "avg_probability": Decimal("0.4622"),
        "avg_latency_ms": Decimal("49.296"),
    }
    conn = _MemConn(lambda sql, params: [row])
    service = PredictionQueryService(connection_factory=lambda: conn)

    metrics = service.metrics()

    assert isinstance(metrics["avg_probability"], float)
    assert metrics["avg_probability"] == 0.4622
    assert metrics["avg_latency_ms"] == 49.296


def test_metrics_sin_datos_no_divide_por_cero():
    conn = _MemConn(lambda sql, params: [])
    service = PredictionQueryService(connection_factory=lambda: conn)

    metrics = service.metrics()

    assert metrics["total"] == 0
    assert metrics["hitl_rate"] == 0.0


def test_divergence_counts_agrega_motivo():
    rows = [
        {"predicted_risk_class": "LOW", "final_action": "BLOCK", "count": 2},
        {"predicted_risk_class": "HIGH", "final_action": "BLOCK", "count": 5},
    ]
    conn = _MemConn(lambda sql, params: rows)
    service = PredictionQueryService(connection_factory=lambda: conn)

    result = service.divergence_counts()

    assert result[0]["divergence"] == "DETERMINISTA_MAS_ESTRICTO"
    assert result[1]["divergence"] == "CONSISTENTE"


def test_temporal_distribution_parametriza_dias():
    conn = _MemConn(lambda sql, params: [])
    service = PredictionQueryService(connection_factory=lambda: conn)

    service.temporal_distribution(days=7)

    _, params = conn.record[0]
    assert params["days"] == 7


def test_model_versions_consulta_registro():
    rows = [
        {
            "model_id": "risk_model_v1",
            "model_version": "v1",
            "algorithm": "logistic",
            "feature_schema_version": "v1",
            "training_dataset_version": "corpus_v1",
            "calibration_method": "isotonic",
            "status": "active",
            "predictions": 12,
        }
    ]
    conn = _MemConn(lambda sql, params: rows)
    service = PredictionQueryService(connection_factory=lambda: conn)

    versions = service.model_versions()

    assert versions[0]["predictions"] == 12


# ── ModelEvidenceService ────────────────────────────────────────────

def test_model_evidence_no_disponible(tmp_path):
    service = ModelEvidenceService(
        model_dir=tmp_path / "nope", corpus_path=tmp_path / "nope.parquet"
    )
    assert service.available is False
    assert service.evaluation_metrics() == {"available": False}
    assert service.metadata() == {}


@requires_artifacts
def test_model_evidence_metricas_reproducibles():
    service = ModelEvidenceService()
    assert service.available is True

    metrics = service.evaluation_metrics()

    assert metrics["available"] is True
    assert metrics["n_evaluation"] == 600
    assert metrics["labels"] == ["LOW", "MEDIUM", "HIGH"]
    # Matriz 3x3 con la suma de la partición de evaluación.
    cm = metrics["confusion_matrix"]
    assert len(cm) == 3 and all(len(r) == 3 for r in cm)
    assert sum(sum(r) for r in cm) == 600
    # Métricas por clase completas.
    assert [c["class"] for c in metrics["per_class"]] == ["LOW", "MEDIUM", "HIGH"]
    assert metrics["macro_f1"] > 0.9
    # Curva de calibración con bins válidos.
    curve = metrics["calibration_curve"]
    assert curve
    for point in curve:
        assert 0.0 <= point["mean_predicted"] <= 1.0
        assert 0.0 <= point["observed_frequency"] <= 1.0
        assert point["count"] > 0


@requires_artifacts
def test_model_evidence_metadata_versionada():
    meta = ModelEvidenceService().metadata()
    assert meta["model_id"] == "risk_model_v1"
    assert meta["feature_schema_version"] == "v1"
    assert meta["training_dataset_version"] == "corpus_v1"
