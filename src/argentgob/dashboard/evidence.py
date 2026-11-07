"""
Sección de evidencia académica del dashboard (Fase 5).

Muestra la trazabilidad corpus → modelo → predicción → decisión:

- Riesgo estimado, clase predicha, probabilidad y versión del modelo.
- Decisión sugerida por el modelo vs decisión final de gobierno.
- Motivo de divergencia entre predicción y decisión.
- Distribución temporal de riesgos.
- Matriz de confusión y curva de calibración (partición de evaluación).
- Tasa de HITL originada por predicción.

INV-05: solo se muestran metadatos y probabilidades, nunca payload crudo.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from argentgob.analytics.evidence import (
    ModelEvidenceService,
    PredictionQueryService,
)

_DIVERGENCE_LABEL = {
    "CONSISTENTE": "✅ Consistente",
    "DETERMINISTA_MAS_ESTRICTO": "🛡️ Determinista más estricto",
    "DETERMINISTA_MENOS_ESTRICTO": "⚠️ Determinista menos estricto",
    "SIN_DECISION": "❓ Sin decisión",
    "SIN_PREDICCION": "— Sin predicción",
}


def render_evidence_section() -> None:
    """Renderiza la sección completa de evidencia predictiva."""
    st.header("🔮 Fase 5 · Evidencia predictiva")
    st.caption(
        "Trazabilidad corpus → modelo → predicción → decisión · "
        "Sin raw payload · INV-05"
    )

    query = PredictionQueryService()
    try:
        metrics = query.metrics()
    except Exception as exc:  # noqa: BLE001 - mostrar error de conexión en la UI
        st.error(f"No se pudo leer la evidencia predictiva: {exc}")
        st.info(
            "Verificá que la migración 006 esté aplicada: "
            "`python -m alembic upgrade head`"
        )
        return

    if not metrics["total"]:
        st.info(
            "Sin predicciones registradas todavía. Ejecutá el agente con el "
            "modelo disponible para generar evidencia."
        )
        _render_model_evidence()
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Predicciones", metrics["total"])
    c2.metric("🟢 LOW", metrics["low"])
    c3.metric("🟡 MEDIUM", metrics["medium"])
    c4.metric("🔴 HIGH", metrics["high"])
    c5.metric("🕐 HITL por predicción", metrics["hitl_from_prediction"])

    c6, c7, c8, c9 = st.columns(4)
    c6.metric("⛔ BLOCK por predicción", metrics["block_from_prediction"])
    c7.metric("Tasa HITL predictiva", f"{100 * metrics['hitl_rate']:.1f}%")
    c8.metric(
        "Latencia media inferencia",
        f"{metrics['avg_latency_ms'] or 0:.2f} ms",
    )
    c9.metric("Sin decisión vinculada", metrics["without_decision"])

    st.divider()

    # ── Predicción vs decisión final ────────────────────────────────
    st.subheader("Predicción vs decisión final")
    with st.expander("Filtros", expanded=False):
        f_class = st.selectbox(
            "Clase predicha", ["", "LOW", "MEDIUM", "HIGH"], key="f5_class"
        )
        f_action = st.selectbox(
            "Decisión final", ["", "PASS", "HITL", "BLOCK"], key="f5_action"
        )
        f_limit = st.slider("Límite", 10, 500, 100, key="f5_limit")

    rows = query.list_predictions(
        predicted_class=f_class or None,
        final_action=f_action or None,
        limit=f_limit,
    )
    if not rows:
        st.info("Sin predicciones para los filtros seleccionados.")
    else:
        df = pd.DataFrame(rows)
        df["divergence"] = df["divergence"].map(
            lambda v: _DIVERGENCE_LABEL.get(v, v)
        )
        cols = [
            "predicted_at",
            "tool_name",
            "agent_role",
            "predicted_risk_class",
            "risk_probability",
            "suggested_action",
            "final_action",
            "final_reason_code",
            "divergence",
            "model_version",
            "inference_latency_ms",
        ]
        st.dataframe(
            df[[c for c in cols if c in df.columns]],
            use_container_width=True,
            height=320,
        )

    st.divider()

    # ── Divergencias agregadas ──────────────────────────────────────
    st.subheader("Motivo de divergencia")
    div_rows = query.divergence_counts()
    if div_rows:
        ddf = pd.DataFrame(div_rows)
        ddf["divergence"] = ddf["divergence"].map(
            lambda v: _DIVERGENCE_LABEL.get(v, v)
        )
        st.dataframe(ddf, use_container_width=True, height=220)
    else:
        st.info("Sin datos de divergencia.")

    st.divider()

    # ── Distribución temporal ───────────────────────────────────────
    st.subheader("Distribución temporal de riesgos")
    days = st.slider("Ventana (días)", 1, 90, 14, key="f5_days")
    temporal = query.temporal_distribution(days=days)
    if temporal:
        tdf = pd.DataFrame(temporal)
        pivot = (
            tdf.pivot(index="day", columns="predicted_risk_class", values="count")
            .fillna(0)
            .sort_index()
        )
        st.bar_chart(pivot)
    else:
        st.info("Sin predicciones en la ventana seleccionada.")

    st.divider()

    # ── Versiones del modelo ────────────────────────────────────────
    st.subheader("Versiones del modelo")
    versions = query.model_versions()
    if versions:
        st.dataframe(pd.DataFrame(versions), use_container_width=True, height=180)
    else:
        st.info("Sin versiones de modelo registradas.")

    st.divider()
    _render_model_evidence()


def _render_model_evidence() -> None:
    """Matriz de confusión y curva de calibración del modelo versionado."""
    st.subheader("Evaluación del modelo (partición congelada)")
    service = ModelEvidenceService()
    if not service.available:
        st.info(
            "Artefactos del modelo o corpus no disponibles en este entorno. "
            "Montá `data/` en el contenedor del dashboard para ver la "
            "evaluación."
        )
        return

    metrics = service.evaluation_metrics()
    if not metrics.get("available"):
        st.info("No se pudieron recalcular las métricas del modelo.")
        return

    st.caption(
        f"Modelo `{metrics['model_id']}` {metrics['model_version']} · "
        f"{metrics['algorithm']} · calibración {metrics['calibration_method']} · "
        f"umbral HIGH {metrics['threshold_high']} · "
        f"n={metrics['n_evaluation']} (evaluación congelada)"
    )

    c1, c2 = st.columns(2)
    c1.metric("Macro F1", metrics["macro_f1"])
    c2.metric("Balanced accuracy", metrics["balanced_accuracy"])

    left, right = st.columns(2)

    with left:
        st.markdown("**Matriz de confusión**")
        cm = pd.DataFrame(
            metrics["confusion_matrix"],
            index=[f"real {c}" for c in metrics["labels"]],
            columns=[f"pred {c}" for c in metrics["labels"]],
        )
        st.dataframe(cm, use_container_width=True)

    with right:
        st.markdown("**Métricas por clase**")
        st.dataframe(
            pd.DataFrame(metrics["per_class"]),
            use_container_width=True,
            height=170,
        )

    st.markdown("**Curva de calibración (probabilidad de HIGH)**")
    curve = metrics.get("calibration_curve") or []
    if curve:
        cdf = pd.DataFrame(curve)
        cdf["bin"] = cdf.apply(
            lambda r: f"{r['bin_lower']:.1f}–{r['bin_upper']:.1f}", axis=1
        )
        st.dataframe(
            cdf[["bin", "mean_predicted", "observed_frequency", "count"]],
            use_container_width=True,
            height=220,
        )
        st.caption(
            "Una curva perfectamente calibrada tiene "
            "`mean_predicted` ≈ `observed_frequency` en cada bin."
        )
    else:
        st.info("Sin bins con datos para la curva de calibración.")
