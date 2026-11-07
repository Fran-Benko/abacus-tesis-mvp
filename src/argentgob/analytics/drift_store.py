"""
DriftStore: persiste reportes de deriva del modelo en PostgreSQL.

Sigue el patron de PredictionStore (fail-open de observabilidad): si la DB no
esta disponible, registra el error pero no propaga la excepcion. La deriva es
metricas, no enforcement.

INV-05: persiste unicamente metadatos, distribuciones y PSI; nunca el payload
crudo (los digests viajan, no los argumentos).

Grants (migracion 007): el rol de aplicacion escribe; los roles de consola
(viewer/auditor/operator) solo leen para visualizacion en el dashboard.
"""
from __future__ import annotations

import json

from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class DriftStore:
    """Escribe y lee reportes de deriva; degrada de forma segura sin DB."""

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or self._default_connection

    @staticmethod
    def _default_connection():
        from argentgob.db.connection import get_connection

        return get_connection()

    def record_report(self, report: dict) -> str:
        """Inserta un reporte de deriva. Retorna el report_id creado."""
        report_id = report.get("report_id") or f"drift-{report.get('generated_at', '')}"
        try:
            conn = self._connection_factory()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO model_drift_reports (
                            report_id, model_id, model_version,
                            feature_schema_version, generated_at,
                            class_psi, feature_psi,
                            class_distribution_baseline, class_distribution_observed,
                            severity_class, severity_global,
                            sample_size_baseline, sample_size_observed,
                            features_compared
                        ) VALUES (
                            %(report_id)s, %(model_id)s, %(model_version)s,
                            %(feature_schema_version)s, now(),
                            %(class_psi)s, %(feature_psi)s,
                            %(class_distribution_baseline)s,
                            %(class_distribution_observed)s,
                            %(severity_class)s, %(severity_global)s,
                            %(sample_size_baseline)s, %(sample_size_observed)s,
                            %(features_compared)s
                        )
                        ON CONFLICT (report_id) DO NOTHING
                        """,
                        {
                            "report_id": report_id,
                            "model_id": report.get("model_id", "risk_model_v1"),
                            "model_version": report.get("model_version", "v1"),
                            "feature_schema_version": report.get(
                                "feature_schema_version", "v1"
                            ),
                            "class_psi": report.get("class_psi", 0.0),
                            "feature_psi": json.dumps(report.get("feature_psi", {})),
                            "class_distribution_baseline": json.dumps(
                                report.get("class_distribution_baseline", {})
                            ),
                            "class_distribution_observed": json.dumps(
                                report.get("class_distribution_observed", {})
                            ),
                            "severity_class": report.get("severity", "sin_deriva"),
                            "severity_global": report.get("severity", "sin_deriva"),
                            "sample_size_baseline": (
                                report.get("sample_sizes", {}).get("baseline", 0)
                            ),
                            "sample_size_observed": (
                                report.get("sample_sizes", {}).get("observed", 0)
                            ),
                            "features_compared": report.get("features_compared", 0),
                        },
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - fail-open de observabilidad
            log.warning(
                "drift_report_persist_failed",
                detail=str(exc),
                report_id=report_id,
            )
        return report_id

    def latest_reports(self, limit: int = 10) -> list[dict]:
        """Ultimos reportes de deriva (para el dashboard)."""
        import psycopg

        try:
            conn = self._connection_factory()
            try:
                with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                    cur.execute(
                        """
                        SELECT
                            report_id, model_id, model_version,
                            feature_schema_version, generated_at,
                            class_psi, feature_psi,
                            class_distribution_baseline, class_distribution_observed,
                            severity_class, severity_global,
                            sample_size_baseline, sample_size_observed,
                            features_compared
                        FROM model_drift_reports
                        ORDER BY generated_at DESC
                        LIMIT %(limit)s
                        """,
                        {"limit": int(limit)},
                    )
                    rows = cur.fetchall()
                for row in rows:
                    for key in (
                        "feature_psi",
                        "class_distribution_baseline",
                        "class_distribution_observed",
                    ):
                        if isinstance(row.get(key), str):
                            try:
                                row[key] = json.loads(row[key])
                            except (TypeError, ValueError):
                                row[key] = {}
                return rows
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - lectura falla sin DB
            log.warning("drift_report_read_failed", detail=str(exc))
            return []
