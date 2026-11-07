"""Monitoreo de deriva del modelo (hito de prioridad media).

Cubre el flujo real del detector: baseline del corpus en memoria vs
distribucion observada desde las predicciones persistidas (store en memoria),
computo del PSI, persistencia del reporte en `model_drift_reports` (store y
lectura via DriftStore) y severidad.

Se usa `_mem_db.py` (misma conexion compartida que el resto de los tests de
flujo) de modo que DriftDetector + DriftStore ven el mismo estado.
"""
from __future__ import annotations

import pytest

from argentgob.analytics.drift import (
    DriftDetector,
    DriftReport,
    load_corpus_baseline,
    observed_class_distribution,
    psi,
    severity,
)
from argentgob.analytics.drift_store import DriftStore
from tests.integration._mem_db import MemConn, _new_store


class _MemDb:
    """Fabrica de conexiones en memoria: comparte el mismo store."""

    def __init__(self):
        self.store = _new_store()

    def factory(self):
        return MemConn(self.store)


def _seed_predictions(db: _MemDb, counts: dict[str, int], prefix: str = "ev") -> None:
    """Puebla risk_predictions con N predicciones por clase (sin decisiones)."""
    seq = 0
    for cls, n in counts.items():
        for _ in range(n):
            seq += 1
            pid = f"pred-{prefix}-{seq}"
            db.store["risk_predictions"][pid] = {
                "prediction_id": pid,
                "event_id": f"{prefix}-{seq}",
                "decision_id": None,
                "model_id": "risk_model_v1",
                "model_version": "v1",
                "predicted_risk_class": cls,
                "risk_probability": 0.5 if cls == "MEDIUM" else (0.1 if cls == "LOW" else 0.9),
                "class_probabilities": "{...}",
                "inference_latency_ms": 1.0,
                "feature_schema_version": "v1",
                "predicted_at": None,
            }


def _class_counts(store) -> dict[str, int]:
    counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for p in store["risk_predictions"].values():
        cls = p["predicted_risk_class"]
        if cls in counts:
            counts[cls] += 1
    return counts


def test_psi_observado_vs_baseline_detecta_deriva():
    """Distribucion observada concentrada en HIGH => PSI significativo."""
    baseline = load_corpus_baseline()  # 50/30/20
    assert baseline["sample_size"] == 3000

    # Observado: 100% HIGH (drift total).
    obs_dist = {"LOW": 0.0, "MEDIUM": 0.0, "HIGH": 1.0}
    value = psi(obs_dist, baseline["class_distribution"])
    assert severity(value) == "significativa"
    assert value > 0.25


def test_psi_observado_igual_baseline_sin_deriva():
    """Distribucion observada identica al corpus => PSI ~ 0."""
    baseline = load_corpus_baseline()
    value = psi(baseline["class_distribution"], baseline["class_distribution"])
    assert value == pytest.approx(0.0, abs=1e-6)
    assert severity(value) == "sin_deriva"


def test_detector_persiste_reporte_y_se_leeria():
    """DriftDetector + DriftStore: el reporte queda persistido y legible."""
    db = _MemDb()
    # Distribucion observada: 100% HIGH -> drift significativo.
    _seed_predictions(db, {"LOW": 0, "MEDIUM": 0, "HIGH": 20})

    baseline = load_corpus_baseline()
    observed = observed_class_distribution(risk_predictions=db.store["risk_predictions"])

    detector = DriftDetector(
        baseline_loader=lambda: baseline,
        observed_provider=lambda: observed,
    )
    report = detector.detect()

    assert isinstance(report, DriftReport)
    assert report.report["class_psi"] > 0.25
    assert report.report["severity"] == "significativa"

    # Persistencia (fail-open): el store en memoria recibe el INSERT.
    store = DriftStore(connection_factory=db.factory)
    report_id = store.record_report(report.report)
    assert report_id == report.report["report_id"]

    persisted = db.store["model_drift_reports"].get(report_id)
    assert persisted is not None
    assert persisted["model_id"] == "risk_model_v1"
    # INV-05: nunca el payload crudo.
    assert "execution_arguments" not in str(persisted)

    # Lectura: ultimo reporte devuelto por DriftStore.
    latest = store.latest_reports(limit=5)
    assert latest, "debe haber al menos un reporte"
    assert latest[0]["report_id"] == report_id


def test_detector_sin_predicciones_degrada_sin_deriva():
    """Sin predicciones observadas, el detector degrada a ceros (no falla)."""
    db = _MemDb()
    baseline = load_corpus_baseline()
    observed = observed_class_distribution(risk_predictions=db.store["risk_predictions"])
    assert observed["sample_size"] == 0

    detector = DriftDetector(
        baseline_loader=lambda: baseline,
        observed_provider=lambda: observed,
    )
    report = detector.detect()
    assert report.report["class_psi"] == 0.0
    assert report.report["severity"] == "sin_deriva"


def test_detector_distribucion_observada_desde_metrics():
    """La observacion via QueryService.metrics() coincide con el store."""
    db = _MemDb()
    _seed_predictions(db, {"LOW": 15, "MEDIUM": 9, "HIGH": 6})

    from argentgob.analytics.evidence import PredictionQueryService

    query = PredictionQueryService(connection_factory=db.factory)
    observed = observed_class_distribution(query_service=query)
    assert observed["sample_size"] == 30
    assert observed["class_distribution"]["LOW"] == pytest.approx(0.5, abs=0.01)
    assert observed["class_distribution"]["MEDIUM"] == pytest.approx(0.3, abs=0.01)
    assert observed["class_distribution"]["HIGH"] == pytest.approx(0.2, abs=0.01)

    # Y con esta distribucion no hay deriva.
    baseline = load_corpus_baseline()
    value = psi(observed["class_distribution"], baseline["class_distribution"])
    assert value < 0.01
