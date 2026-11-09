"""Vista 3 · Inteligencia Predictiva.

Dos secciones visualmente separadas que jamás se mezclan (A6):
1. Producción (`PredictionQueryService`, N=85).
2. Evaluación congelada (`ModelEvidenceService`, n=600) — rotulada como tal.

`risk_probability` se rotula «Confianza de clase» (A4): es el score calibrado
de la clase predicha, no P(HIGH) acumulada. El umbral 0.3 pertenece a la
evaluación congelada.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from argentgob.analytics.evidence import ModelEvidenceService, PredictionQueryService
from argentgob.dashboard import charts, components, queries

_DIVERGENCE_LABEL = {
    "CONSISTENTE": "Consistente",
    "DETERMINISTA_MAS_ESTRICTO": "Determinista más estricto",
    "DETERMINISTA_MENOS_ESTRICTO": "Determinista menos estricto",
    "SIN_DECISION": "Sin decisión",
    "SIN_PREDICCION": "Sin predicción",
}


def _render_produccion():
    """Sección Producción (N=85)."""
    st.subheader("Producción")
    st.caption("Predicciones en runtime · No incluye la evaluación congelada")
    query = PredictionQueryService()
    metrics = query.metrics()

    st.columns(5)
    components.kpi("Predicciones", metrics["total"])
    components.kpi("LOW", metrics["low"])
    components.kpi("MEDIUM", metrics["medium"])
    components.kpi("HIGH", metrics["high"])
    components.kpi(
        "HITL por predicción",
        metrics["hitl_from_prediction"],
        caption=f"Tasa {100 * metrics['hitl_rate']:.1f}%",
    )

    st.columns(4)
    components.kpi("BLOCK por predicción", metrics["block_from_prediction"])
    components.kpi(
        "Latencia media inferencia",
        f"{metrics['avg_latency_ms']:.2f} ms" if metrics["avg_latency_ms"] else "No disponible",
        caption="Promedio aritmético",
    )
    components.kpi(
        "Sin decisión vinculada",
        metrics["without_decision"],
        caption="Predicciones sin decisión final asociada",
    )

    # Heatmap Predicción × Decisión + Motivos de divergencia (A11).
    div = query.divergence_counts()
    if div:
        ddf = pd.DataFrame(div)
        ddf["divergence"] = ddf["divergence"].map(
            lambda v: _DIVERGENCE_LABEL.get(v, v)
        )
        heat = (
            ddf.pivot(
                index="predicted_risk_class",
                columns="final_action",
                values="count",
            )
            .fillna(0)
            .reindex(columns=["PASS", "HITL", "BLOCK"], fill_value=0)
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Predicción × Decisión final**")
            st.echarts_chart(
                charts.heatmap_prediccion_decision(heat),
                theme="streamlit",
                renderer="svg",
                key="pi_heat",
            )
        with c2:
            st.markdown("**Motivos de divergencia**")
            st.echarts_chart(
                charts.motivos_divergencia(ddf),
                theme="streamlit",
                renderer="svg",
                key="pi_div",
            )

    # Tabla filtrable con nulos saneados (A5) y risk_probability rotulada (A4).
    st.markdown("**Detalle de predicciones**")
    with st.expander("Filtros", expanded=False):
        f_class = st.selectbox(
            "Clase predicha", ["", "LOW", "MEDIUM", "HIGH"], key="pi_class"
        )
        f_action = st.selectbox(
            "Decisión final", ["", "PASS", "HITL", "BLOCK"], key="pi_action"
        )
        f_limit = st.slider("Límite", 10, 500, 100, key="pi_limit")

    rows = query.list_predictions(
        predicted_class=f_class or None,
        final_action=f_action or None,
        limit=f_limit,
    )
    if not rows:
        st.info("Sin predicciones para los filtros seleccionados.")
    else:
        df = queries.predicciones_saneadas(rows)
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
            column_config={
                "risk_probability": st.column_config.NumberColumn(
                    "Confianza de clase",
                    help="Score calibrado de la clase predicha (no P(HIGH) acumulada)",
                    format="%.4f",
                )
            },
        )


def _render_evaluacion():
    """Sección Evaluación congelada (n=600)."""
    st.subheader("Evaluación congelada")
    st.caption(
        "Evaluación congelada (n=600) · No representa tráfico en vivo"
    )
    service = ModelEvidenceService()
    if not service.available:
        st.info(
            "Artefactos del modelo o corpus no disponibles en este entorno. "
            "Montá `data/` en el contenedor del dashboard para ver la evaluación."
        )
        return

    metrics = service.evaluation_metrics()
    if not metrics.get("available"):
        st.info("No se pudieron recalcular las métricas del modelo.")
        return

    st.caption(
        f"Modelo `{metrics['model_id']}` {metrics['model_version']} · "
        f"{metrics['algorithm']} · calibración {metrics['calibration_method']} · "
        f"umbral HIGH {metrics['threshold_high']} (alcance: evaluación congelada) · "
        f"n={metrics['n_evaluation']}"
    )

    st.columns(2)
    components.kpi("Macro F1", metrics["macro_f1"])
    components.kpi("Balanced accuracy", metrics["balanced_accuracy"])

    left, right = st.columns(2)
    with left:
        st.markdown("**Matriz de confusión**")
        cm = metrics["confusion_matrix"]
        labels = metrics["labels"]
        st.echarts_chart(
            charts.matriz_confusion(cm, labels),
            theme="streamlit",
            renderer="svg",
            key="pi_cm",
        )
    with right:
        st.markdown("**Métricas por clase**")
        st.dataframe(
            pd.DataFrame(metrics["per_class"]),
            use_container_width=True,
            height=170,
        )

    st.markdown("**Curva de calibración**")
    curve = metrics.get("calibration_curve") or []
    if curve:
        st.echarts_chart(
            charts.curva_calibracion(curve),
            theme="streamlit",
            renderer="svg",
            key="pi_cal",
        )
        st.caption(
            "Una curva perfectamente calibrada tiene `mean_predicted` ≈ "
            "`observed_frequency` en cada bin."
        )
    else:
        st.info("Sin bins con datos para la curva de calibración.")


def render() -> None:
    """Renderiza la vista Inteligencia Predictiva."""
    st.title("Inteligencia Predictiva")
    st.caption(
        "Producción y evaluación congelada separadas · Sin raw payload · INV-05"
    )
    _render_produccion()
    st.divider()
    _render_evaluacion()