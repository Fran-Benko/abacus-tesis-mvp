"""
Monitoreo de deriva del modelo (Fase 5+ — hito de prioridad media).

Implementa el PSI (Population Stability Index) entre una distribucion de
referencia (baseline) y la distribucion observada en produccion, para dos
niveles:

- **Classes de riesgo**: distribucion de `predicted_risk_class` observada en
  `risk_predictions` vs la distribucion del corpus de entrenamiento
  (`corpus_v1.parquet`) o la registrada en `risk_model_versions`.
- **Features**: distribucion de features canonicas (FEATURE_ORDER) del corpus
  vs las features observadas en produccion (construidas por `FeatureBuilder`).

Reglas de severidad (convencion estandar del PSI):
- PSI < 0.10  -> sin deriva significativa.
- 0.10 <= PSI < 0.25 -> deriva moderada (alerar).
- PSI >= 0.25 -> deriva significativa (accion requerida).

Invariantes:
- El PSI se computa sobre distribuciones de clases/features, nunca sobre
  payloads crudos (INV-05: solo metadatos; los digests viajan, no el payload).
- La persistencia del reporte es fail-open: si la DB no esta disponible, se
  registra el error sin propagarlo (mismo patron que PredictionStore).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argentgob.analytics.features import FEATURE_ORDER
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

# Severidad del PSI por convencion estandar.
PSI_NO_DRIFT = 0.10
PSI_MODERATE = 0.25

# Clases de riesgo en el orden canonico.
RISK_CLASSES = ["LOW", "MEDIUM", "HIGH"]

# Prefijos de features que se pueden comparar de forma homogenea.
_FEATURE_SCHEMA_VERSION = "v1"


def psi(candidate: dict[str, float], reference: dict[str, float]) -> float:
    """PSI entre dos distribuciones de probabilidad (diccionarios de clase→p).

    PSI = sum((actual - expected) * ln(actual / expected))
    Se aplica la correccion estandar de bins vacios (0 -> epsilon) para evitar
    division por cero / ln(0). Si `candidate` o `reference` estan vacios, o
    alguna de las dos no tiene masa (suma 0), devuelve 0.0 (no hay deriva
    medible con una distribucion sin observaciones).
    """
    if not candidate or not reference:
        return 0.0
    if sum(candidate.values()) == 0.0 or sum(reference.values()) == 0.0:
        return 0.0
    keys = set(candidate) | set(reference)
    total = 0.0
    for k in keys:
        p = max(candidate.get(k, 0.0), 1e-9)
        q = max(reference.get(k, 0.0), 1e-9)
        total += (p - q) * math.log(p / q)
    return round(total, 6)


def psi_from_counts(
    candidate_counts: dict[str, int], reference_counts: dict[str, int]
) -> float:
    """PSI a partir de conteos crudos (normaliza internamente)."""
    cand_total = sum(candidate_counts.values())
    ref_total = sum(reference_counts.values())
    if cand_total == 0 or ref_total == 0:
        return 0.0
    cand = {k: v / cand_total for k, v in candidate_counts.items()}
    ref = {k: v / ref_total for k, v in reference_counts.items()}
    return psi(cand, ref)


def severity(psi_value: float) -> str:
    """Clasifica el PSI en sin_deriva / moderada / significativa."""
    if psi_value < PSI_NO_DRIFT:
        return "sin_deriva"
    if psi_value < PSI_MODERATE:
        return "moderada"
    return "significativa"


class DriftReport:
    """Reporte de deriva: distribuciones comparadas, PSI global y por feature."""

    def __init__(
        self,
        model_id: str,
        model_version: str,
        baseline: dict[str, Any],
        observed: dict[str, Any],
    ):
        import hashlib

        self.model_id = model_id
        self.model_version = model_version
        self.baseline = baseline
        self.observed = observed
        # Fija el momento de generacion una sola vez para que el report_id y el
        # timestamp sean estables entre lecturas del reporte.
        self._generated_at = datetime.now(UTC)
        digest = hashlib.sha256(
            f"{model_id}:{model_version}:{self._generated_at.isoformat()}".encode()
        ).hexdigest()[:12]
        self._report_id = f"drift-{digest}"

    @property
    def report(self) -> dict[str, Any]:
        """Construye el dict serializable del reporte (INV-05: sin payload)."""
        return {
            "report_id": self._report_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "feature_schema_version": _FEATURE_SCHEMA_VERSION,
            "generated_at": self._generated_at.isoformat(),
            "class_psi": self._class_psi(),
            "class_distribution_baseline": self.baseline.get("class_distribution", {}),
            "class_distribution_observed": self.observed.get("class_distribution", {}),
            "feature_psi": self._feature_psi(),
            "severity": severity(self._class_psi()),
            "sample_sizes": {
                "baseline": self.baseline.get("sample_size", 0),
                "observed": self.observed.get("sample_size", 0),
            },
            "features_compared": len(self.baseline.get("feature_psi_support", [])),
        }

    @property
    def summary(self) -> dict[str, Any]:
        """Version reducida para consumo puntual (dashboard/CLI)."""
        r = self.report
        return {
            "model_id": r["model_id"],
            "model_version": r["model_version"],
            "generated_at": r["generated_at"],
            "class_psi": r["class_psi"],
            "feature_psi": r["feature_psi"],
            "severity": r["severity"],
            "class_distribution_observed": r["class_distribution_observed"],
        }

    def _class_psi(self) -> float:
        return psi(
            self.observed.get("class_distribution", {}),
            self.baseline.get("class_distribution", {}),
        )

    def _feature_psi(self) -> dict[str, float]:
        """PSI por feature (solo features con datos en ambos lados)."""
        support = self.baseline.get("feature_psi_support", [])
        result: dict[str, float] = {}
        for feat in support:
            base = self.baseline.get("feature_distributions", {}).get(feat, {})
            obs = self.observed.get("feature_distributions", {}).get(feat, {})
            if base and obs:
                result[feat] = psi(obs, base)
        return result


class DriftDetector:
    """Genera un DriftReport comparando baseline y ventana observada."""

    def __init__(
        self,
        baseline_loader: Callable[[], dict[str, Any]],
        observed_provider: Callable[[], dict[str, Any]],
    ):
        self._baseline_loader = baseline_loader
        self._observed_provider = observed_provider

    def detect(self) -> DriftReport:
        """Computa el reporte de deriva actual."""
        baseline = self._baseline_loader()
        observed = self._observed_provider()
        return DriftReport(
            model_id=baseline.get("model_id", "risk_model_v1"),
            model_version=baseline.get("model_version", "v1"),
            baseline=baseline,
            observed=observed,
        )


def build_class_distribution(
    counts: dict[str, int], classes: list[str] | None = None
) -> dict[str, float]:
    """Normaliza conteos por clase a una distribucion de probabilidad."""
    classes = classes or RISK_CLASSES
    total = sum(counts.values())
    if total == 0:
        return {c: 0.0 for c in classes}
    return {c: round(counts.get(c, 0) / total, 6) for c in classes}


def load_corpus_baseline(corpus_path: str | None = None) -> dict[str, Any]:
    """Carga la distribucion de clases y features de features del corpus.

    Usa el parquet de entrenamiento (`corpus_v1.parquet`) como distribucion de
    referencia del modelo entrenado. Las features son las columnas canonicas
    (`FEATURE_ORDER`), con distribucion discreta por feature para features
    categoricas/boleaanas y cuantiles para las numericas.

    Invariante: solo lectura; no muta el corpus. Si el corpus no existe o no
    tiene las columnas esperadas, degrada a un baseline vacio (la deteccion
    quedara en `sin_deriva` con 0 features comparadas).
    """
    import pandas as pd

    if corpus_path is None:
        corpus_path = (
            Path(__file__).resolve().parents[3] / "data" / "corpus" / "corpus_v1.parquet"
        )
    try:
        df = pd.read_parquet(corpus_path)
    except Exception as exc:  # noqa: BLE001 - degradacion segura
        log.warning("drift_corpus_unavailable", detail=str(exc), path=str(corpus_path))
        return _empty_baseline()

    label_col = "label.risk_class"
    if label_col not in df.columns:
        return _empty_baseline()

    counts = df[label_col].value_counts().to_dict()
    class_dist = build_class_distribution({str(k): int(v) for k, v in counts.items()})

    feature_distributions: dict[str, dict[str, float]] = {}
    support: list[str] = []
    for feat in FEATURE_ORDER:
        if feat not in df.columns:
            continue
        series = df[feat]
        if series.dtype.kind in "biufc" or series.dtype == object:
            # Distribucion de frecuencias discretas (normalizada).
            vc = series.astype(str).value_counts().to_dict()
            dist = {str(k): float(v) for k, v in vc.items()}
            total = sum(dist.values())
            if total:
                dist = {k: round(v / total, 6) for k, v in dist.items()}
                feature_distributions[feat] = dist
                support.append(feat)

    return {
        "model_id": "risk_model_v1",
        "model_version": "v1",
        "feature_schema_version": _FEATURE_SCHEMA_VERSION,
        "class_distribution": class_dist,
        "feature_distributions": feature_distributions,
        "feature_psi_support": support,
        "sample_size": len(df),
    }


def observed_class_distribution(
    query_service=None, risk_predictions: dict | None = None
) -> dict[str, Any]:
    """Distribucion observada de clases desde las predicciones persistidas.

    Recibe o un `PredictionQueryService` (lectura via `metrics()`) o el
    `store["risk_predictions"]` en memoria de `_mem_db.py`. Si no hay datos,
    retorna una distribucion vacia (ceros).
    """
    if risk_predictions is not None:
        counts: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        for p in risk_predictions.values():
            cls = p.get("predicted_risk_class")
            if cls in counts:
                counts[cls] += 1
        return {
            "class_distribution": build_class_distribution(counts),
            "sample_size": sum(counts.values()),
            "feature_distributions": {},
            "feature_psi_support": [],
        }

    if query_service is not None:
        try:
            metrics = query_service.metrics()
            counts = {
                "LOW": metrics.get("low", 0),
                "MEDIUM": metrics.get("medium", 0),
                "HIGH": metrics.get("high", 0),
            }
            return {
                "class_distribution": build_class_distribution(counts),
                "sample_size": metrics.get("total", 0),
                "feature_distributions": {},
                "feature_psi_support": [],
            }
        except Exception as exc:  # noqa: BLE001 - degradacion segura
            log.warning("drift_observed_unavailable", detail=str(exc))

    return _empty_observed()


def _empty_observed() -> dict[str, Any]:
    return {
        "class_distribution": {c: 0.0 for c in RISK_CLASSES},
        "sample_size": 0,
        "feature_distributions": {},
        "feature_psi_support": [],
    }


def _empty_baseline() -> dict[str, Any]:
    return {
        "model_id": "risk_model_v1",
        "model_version": "v1",
        "feature_schema_version": _FEATURE_SCHEMA_VERSION,
        "class_distribution": {c: 0.0 for c in RISK_CLASSES},
        "feature_distributions": {},
        "feature_psi_support": [],
        "sample_size": 0,
    }
