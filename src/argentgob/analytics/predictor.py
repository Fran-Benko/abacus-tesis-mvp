"""
RiskPredictor: carga el modelo entrenado (Fase 3) y predice el riesgo de una
tool call pre-ejecución.

Fase 4 — Integración operacional. Usa los artefactos versionados de
`data/models/risk_model_v1/`:
- `pipeline.joblib`: pipeline (preprocesador + clasificador calibrado).
- `thresholds.json`: umbral de probabilidad HIGH y clase por defecto.
- `model_meta.json`: metadatos (model_id, version, feature_schema_version,
  training_dataset_version, artifact_digest).

Contrato de inferencia:
- `predict_proba` devuelve probabilidades en orden LOW/MEDIUM/HIGH (el
  entrenador normaliza el orden de `classes_`).
- `predict` aplica el umbral HIGH: si la clase predicha es HIGH pero su
  probabilidad < umbral, se degrada a LOW (mismo criterio que el entrenador).
- Si los artefactos no están disponibles o la inferencia falla, `predict`
  retorna `None` (fallback fail-closed: el PEP decide sin predicción).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argentgob.analytics.features import FEATURE_ORDER
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

TARGET_ORDER = ["LOW", "MEDIUM", "HIGH"]
DEFAULT_MODEL_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "models" / "risk_model_v1"
)


def resolve_model_dir() -> Path:
    """Directorio de artefactos del predictor (AV-02).

    `RISK_MODEL_DIR` permite montar los artefactos RO en la imagen sin depender
    del layout del repositorio; nunca se acepta una ruta suministrada por el
    cliente de la API.
    """
    import os

    configured = os.environ.get("RISK_MODEL_DIR")
    return Path(configured) if configured else DEFAULT_MODEL_DIR


@dataclass
class RiskPrediction:
    """Resultado de la inferencia predictiva."""

    predicted_risk_class: str
    risk_probability: float
    class_probabilities: dict[str, float] = field(default_factory=dict)
    model_id: str = "risk_model_v1"
    model_version: str = "v1"
    feature_schema_version: str = "v1"
    inference_latency_ms: float = 0.0


class RiskPredictor:
    """Carga el pipeline y predice el riesgo de una tool call."""

    def __init__(self, model_dir: Path | str | None = None):
        self.model_dir = Path(model_dir) if model_dir else resolve_model_dir()
        self._pipeline: Any = None
        self._threshold: float = 0.3
        self._default_class: str = "LOW"
        self._meta: dict[str, Any] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        """Carga pipeline, umbrales y metadatos. Degrada a no-disponible."""
        try:
            pipeline_path = self.model_dir / "pipeline.joblib"
            thresholds_path = self.model_dir / "thresholds.json"
            meta_path = self.model_dir / "model_meta.json"
            if not (pipeline_path.exists() and thresholds_path.exists()):
                log.warning(
                    "riskpredictor_artifacts_missing",
                    model_dir=str(self.model_dir),
                )
                return
            import joblib

            self._pipeline = joblib.load(pipeline_path)
            thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
            self._threshold = float(thresholds.get("threshold_high", 0.3))
            self._default_class = thresholds.get("default_class", "LOW")
            if meta_path.exists():
                self._meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self._loaded = True
            log.info(
                "riskpredictor_loaded",
                model_id=self._meta.get("model_id", "risk_model_v1"),
                model_version=self._meta.get("model_version", "v1"),
                threshold=self._threshold,
            )
        except Exception as exc:  # noqa: BLE001 - degradación segura
            log.warning(
                "riskpredictor_load_failed",
                detail=str(exc),
                model_dir=str(self.model_dir),
            )
            self._loaded = False

    @property
    def available(self) -> bool:
        """True si el pipeline está cargado y listo para inferir."""
        return self._loaded and self._pipeline is not None

    @property
    def model_id(self) -> str:
        return self._meta.get("model_id", "risk_model_v1")

    @property
    def model_version(self) -> str:
        return self._meta.get("model_version", "v1")

    @property
    def feature_schema_version(self) -> str:
        return self._meta.get("feature_schema_version", "v1")

    @property
    def threshold_high(self) -> float:
        return self._threshold

    def predict_proba(self, features: dict[str, Any]) -> list[float] | None:
        """Retorna [pLOW, pMEDIUM, pHIGH] o None si no disponible/falla."""
        if not self.available:
            return None
        try:
            import pandas as pd

            row = {k: features.get(k) for k in FEATURE_ORDER}
            X = pd.DataFrame([row], columns=FEATURE_ORDER)
            proba = self._pipeline.predict_proba(X)[0]
            # Normalizar al orden LOW/MEDIUM/HIGH. El pipeline calibrado
            # expone classes_ como enteros (0=LOW, 1=MEDIUM, 2=HIGH).
            ordered = [0.0, 0.0, 0.0]
            for i, cls in enumerate(self._pipeline.classes_):
                try:
                    idx = int(cls)  # np.int64 / int / str
                except (TypeError, ValueError):
                    idx = TARGET_ORDER.index(str(cls))
                if 0 <= idx < 3:
                    ordered[idx] = float(proba[i])
            return ordered
        except Exception as exc:  # noqa: BLE001 - fallback fail-closed
            log.warning(
                "riskpredictor_inference_failed",
                detail=str(exc),
            )
            return None

    def predict(self, features: dict[str, Any]) -> RiskPrediction | None:
        """Predice la clase de riesgo aplicando el umbral HIGH.

        Retorna None si el modelo no está disponible o la inferencia falla
        (el PEP degrada a fallback determinista fail-closed).
        """
        import time

        if not self.available:
            return None
        started = time.perf_counter()
        proba = self.predict_proba(features)
        if proba is None:
            return None
        latency_ms = (time.perf_counter() - started) * 1000.0

        p_low, p_med, p_high = proba
        # Clase argmax.
        idx = max(range(3), key=lambda i: proba[i])
        predicted = TARGET_ORDER[idx]
        # Umbral HIGH: si la clase predicha es HIGH pero prob < umbral,
        # degradar a la clase por defecto (mismo criterio que el entrenador).
        if predicted == "HIGH" and p_high < self._threshold:
            predicted = self._default_class

        return RiskPrediction(
            predicted_risk_class=predicted,
            risk_probability=round(p_high, 6),
            class_probabilities={
                "LOW": round(p_low, 6),
                "MEDIUM": round(p_med, 6),
                "HIGH": round(p_high, 6),
            },
            model_id=self.model_id,
            model_version=self.model_version,
            feature_schema_version=self.feature_schema_version,
            inference_latency_ms=round(latency_ms, 4),
        )
