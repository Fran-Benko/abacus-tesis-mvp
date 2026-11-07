"""
Evidencia académica de la capa predictiva (Fase 5).

Dos servicios independientes:

- `PredictionQueryService`: lecturas parametrizadas sobre la vista
  `v_risk_evidence` (migración 006), que cruza `risk_predictions` con la
  decisión final de gobierno. Permite mostrar riesgo estimado, clase,
  probabilidad, versión del modelo, decisión sugerida vs decisión final,
  motivo de divergencia, distribución temporal y tasa de HITL originada por
  predicción.

- `ModelEvidenceService`: métricas reproducibles del modelo versionado sobre
  la partición de evaluación congelada del corpus (matriz de confusión, curva
  de calibración, métricas por clase y latencia). No consulta la base: lee los
  artefactos de `data/models/risk_model_v1/` y `data/corpus/corpus_v1.parquet`.

INV-05: ninguna consulta expone `execution_arguments` ni payload crudo; solo
metadatos, probabilidades y digests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from psycopg.rows import dict_row

from argentgob.db.connection import get_connection
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

TARGET_ORDER = ["LOW", "MEDIUM", "HIGH"]

# Decisión sugerida por el modelo para cada clase de riesgo.
SUGGESTED_ACTION = {"LOW": "PASS", "MEDIUM": "HITL", "HIGH": "BLOCK"}

# Severidad de la decisión final (para explicar divergencias).
_ACTION_RANK = {"PASS": 0, "HITL": 1, "BLOCK": 2}

DEFAULT_MODEL_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "models" / "risk_model_v1"
)
DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "corpus" / "corpus_v1.parquet"
)


def _as_float(value: Any) -> float | None:
    """Normaliza agregados numéricos (Decimal de PostgreSQL) a float."""
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def divergence_reason(
    predicted_class: str | None, final_action: str | None
) -> str:
    """Explica la relación entre la decisión sugerida y la decisión final.

    Retorna uno de: CONSISTENTE, DETERMINISTA_MAS_ESTRICTO,
    DETERMINISTA_MENOS_ESTRICTO, SIN_DECISION, SIN_PREDICCION.
    """
    if predicted_class is None:
        return "SIN_PREDICCION"
    if final_action is None:
        return "SIN_DECISION"
    suggested = SUGGESTED_ACTION.get(predicted_class)
    if suggested is None:
        return "SIN_PREDICCION"
    if suggested == final_action:
        return "CONSISTENTE"
    if _ACTION_RANK.get(final_action, 0) > _ACTION_RANK.get(suggested, 0):
        return "DETERMINISTA_MAS_ESTRICTO"
    return "DETERMINISTA_MENOS_ESTRICTO"


class PredictionQueryService:
    """Consultas sanitizadas de predicciones cruzadas con la decisión final."""

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self._connection_factory = connection_factory or (lambda: get_connection())

    def list_predictions(
        self,
        *,
        predicted_class: str | None = None,
        final_action: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lista predicciones con la decisión final asociada (parametrizado)."""
        where: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if predicted_class:
            where.append("predicted_risk_class = %(predicted_class)s")
            params["predicted_class"] = predicted_class
        if final_action:
            where.append("final_action = %(final_action)s")
            params["final_action"] = final_action

        sql = """
            SELECT
                prediction_id,
                event_id,
                decision_id,
                model_id,
                model_version,
                predicted_risk_class,
                risk_probability,
                class_probabilities,
                inference_latency_ms,
                feature_schema_version,
                predicted_at,
                final_action,
                final_reason_code,
                tool_name,
                agent_role,
                operation_class,
                decided_at
            FROM v_risk_evidence
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY predicted_at DESC LIMIT %(limit)s OFFSET %(offset)s"

        rows = self._fetch(sql, params)
        for row in rows:
            row["suggested_action"] = SUGGESTED_ACTION.get(
                row.get("predicted_risk_class")
            )
            row["divergence"] = divergence_reason(
                row.get("predicted_risk_class"), row.get("final_action")
            )
        return rows

    def metrics(self) -> dict[str, Any]:
        """Agregados: total, distribución por clase, divergencias y HITL."""
        sql = """
            SELECT
                COUNT(*)                                        AS total,
                COUNT(*) FILTER (WHERE predicted_risk_class = 'LOW')    AS low,
                COUNT(*) FILTER (WHERE predicted_risk_class = 'MEDIUM') AS medium,
                COUNT(*) FILTER (WHERE predicted_risk_class = 'HIGH')   AS high,
                COUNT(*) FILTER (WHERE decision_id IS NULL)             AS without_decision,
                COUNT(*) FILTER (
                    WHERE decision_id IS NOT NULL AND final_action IS NULL
                )                                                       AS unlinked_decision,
                COUNT(*) FILTER (
                    WHERE final_action = 'HITL'
                      AND final_reason_code = 'PREDICTED_MEDIUM_RISK'
                ) AS hitl_from_prediction,
                COUNT(*) FILTER (
                    WHERE final_action = 'BLOCK'
                      AND final_reason_code = 'PREDICTED_HIGH_RISK'
                ) AS block_from_prediction,
                ROUND(AVG(risk_probability)::numeric, 4)        AS avg_probability,
                ROUND(AVG(inference_latency_ms)::numeric, 3)    AS avg_latency_ms
            FROM v_risk_evidence
        """
        rows = self._fetch(sql, {})
        counts = rows[0] if rows else {}
        total = counts.get("total", 0) or 0
        hitl_pred = counts.get("hitl_from_prediction", 0) or 0
        return {
            "total": total,
            "low": counts.get("low", 0) or 0,
            "medium": counts.get("medium", 0) or 0,
            "high": counts.get("high", 0) or 0,
            "without_decision": counts.get("without_decision", 0) or 0,
            "unlinked_decision": counts.get("unlinked_decision", 0) or 0,
            "hitl_from_prediction": hitl_pred,
            "block_from_prediction": counts.get("block_from_prediction", 0) or 0,
            "hitl_rate": round(hitl_pred / total, 4) if total else 0.0,
            "avg_probability": _as_float(counts.get("avg_probability")),
            "avg_latency_ms": _as_float(counts.get("avg_latency_ms")),
        }

    def divergence_counts(self) -> list[dict[str, Any]]:
        """Conteo de divergencias predicción vs decisión final."""
        sql = """
            SELECT
                predicted_risk_class,
                final_action,
                COUNT(*) AS count
            FROM v_risk_evidence
            GROUP BY predicted_risk_class, final_action
            ORDER BY predicted_risk_class, final_action
        """
        rows = self._fetch(sql, {})
        for row in rows:
            row["suggested_action"] = SUGGESTED_ACTION.get(
                row.get("predicted_risk_class")
            )
            row["divergence"] = divergence_reason(
                row.get("predicted_risk_class"), row.get("final_action")
            )
        return rows

    def temporal_distribution(self, *, days: int = 14) -> list[dict[str, Any]]:
        """Distribución temporal de riesgos por día y clase."""
        sql = """
            SELECT
                date_trunc('day', predicted_at)::date AS day,
                predicted_risk_class,
                COUNT(*) AS count
            FROM v_risk_evidence
            WHERE predicted_at >= now() - (%(days)s || ' days')::interval
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        return self._fetch(sql, {"days": int(days)})

    def model_versions(self) -> list[dict[str, Any]]:
        """Versiones del modelo registradas y su uso observado."""
        sql = """
            SELECT
                v.model_id,
                v.model_version,
                v.algorithm,
                v.feature_schema_version,
                v.training_dataset_version,
                v.calibration_method,
                v.status,
                COUNT(p.prediction_id) AS predictions
            FROM risk_model_versions v
            LEFT JOIN risk_predictions p
                ON p.model_id = v.model_id AND p.model_version = v.model_version
            GROUP BY 1, 2, 3, 4, 5, 6, 7
            ORDER BY v.model_id, v.model_version
        """
        return self._fetch(sql, {})

    def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        conn = self._connection_factory()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


class ModelEvidenceService:
    """Métricas reproducibles del modelo sobre la partición de evaluación."""

    def __init__(
        self,
        model_dir: Path | str | None = None,
        corpus_path: Path | str | None = None,
    ):
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self.corpus_path = Path(corpus_path) if corpus_path else DEFAULT_CORPUS_PATH

    @property
    def available(self) -> bool:
        return (
            (self.model_dir / "pipeline.joblib").exists()
            and self.corpus_path.exists()
        )

    def metadata(self) -> dict[str, Any]:
        """Metadatos versionados del modelo (model_meta.json)."""
        path = self.model_dir / "model_meta.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def evaluation_metrics(self) -> dict[str, Any]:
        """Métricas de la partición de evaluación congelada.

        Recalcula matriz de confusión, métricas por clase y curva de
        calibración a partir del pipeline versionado y del corpus, de modo que
        la evidencia sea reproducible y no dependa de valores hardcodeados.
        """
        if not self.available:
            return {"available": False}

        import joblib
        import numpy as np
        import pandas as pd
        from sklearn.metrics import (
            balanced_accuracy_score,
            confusion_matrix,
            f1_score,
            precision_recall_fscore_support,
            recall_score,
        )

        from argentgob.analytics.features import FEATURE_ORDER

        meta = self.metadata()
        threshold = float(
            meta.get("metrics_evaluation", {}).get("threshold_high", 0.3)
        )
        pipeline = joblib.load(self.model_dir / "pipeline.joblib")
        df = pd.read_parquet(self.corpus_path)
        df = df[df["split"] == "evaluation"]
        if df.empty:
            return {"available": False}

        X = df[FEATURE_ORDER]
        y_true = df["label.risk_class"].to_numpy()

        proba = pipeline.predict_proba(X)
        classes = list(pipeline.classes_)
        expected = [0, 1, 2]
        if classes != expected:
            order = [classes.index(v) for v in expected]
            proba = proba[:, order]

        pred_idx = np.argmax(proba, axis=1)
        pred_idx = np.where(
            (pred_idx == 2) & (proba[:, 2] < threshold), 0, pred_idx
        )
        y_pred = np.array([TARGET_ORDER[int(i)] for i in pred_idx])

        cm = confusion_matrix(y_true, y_pred, labels=TARGET_ORDER)
        recall = recall_score(
            y_true, y_pred, labels=TARGET_ORDER, average=None, zero_division=0
        )
        precision, _, f1, support = precision_recall_fscore_support(
            y_true, y_pred, labels=TARGET_ORDER, zero_division=0
        )

        return {
            "available": True,
            "model_id": meta.get("model_id"),
            "model_version": meta.get("model_version"),
            "algorithm": meta.get("algorithm"),
            "calibration_method": meta.get("calibration_method"),
            "threshold_high": threshold,
            "n_evaluation": int(len(df)),
            "macro_f1": round(
                float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4
            ),
            "balanced_accuracy": round(
                float(balanced_accuracy_score(y_true, y_pred)), 4
            ),
            "confusion_matrix": cm.tolist(),
            "labels": TARGET_ORDER,
            "per_class": [
                {
                    "class": c,
                    "precision": round(float(p), 4),
                    "recall": round(float(r), 4),
                    "f1": round(float(f), 4),
                    "support": int(s),
                }
                for c, p, r, f, s in zip(
                    TARGET_ORDER, precision, recall, f1, support
                )
            ],
            "calibration_curve": self._calibration_curve(
                proba[:, 2], (y_true == "HIGH").astype(int)
            ),
        }

    @staticmethod
    def _calibration_curve(
        p_high: Any, y_high: Any, bins: int = 10
    ) -> list[dict[str, Any]]:
        """Curva de confiabilidad para la probabilidad de HIGH."""
        import numpy as np

        edges = np.linspace(0.0, 1.0, bins + 1)
        curve: list[dict[str, Any]] = []
        for i in range(bins):
            lo, hi = edges[i], edges[i + 1]
            mask = (p_high >= lo) & (p_high < hi) if i < bins - 1 else (
                (p_high >= lo) & (p_high <= hi)
            )
            n = int(mask.sum())
            if n == 0:
                continue
            curve.append(
                {
                    "bin_lower": round(float(lo), 2),
                    "bin_upper": round(float(hi), 2),
                    "mean_predicted": round(float(p_high[mask].mean()), 4),
                    "observed_frequency": round(float(y_high[mask].mean()), 4),
                    "count": n,
                }
            )
        return curve
