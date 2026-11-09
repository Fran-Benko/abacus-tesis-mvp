"""Vista 4 · Salud del Modelo (deriva).

PSI de clases global, severidad, features comparables (como cobertura, no como
cálculo por feature — B8), barras agrupadas baseline vs observada, y tabla
histórica de reportes con estado vacío profesional.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from argentgob.analytics.drift import (
    load_corpus_baseline,
    psi_from_counts,
    severity,
)
from argentgob.analytics.drift_store import DriftStore
from argentgob.analytics.evidence import PredictionQueryService
from argentgob.dashboard import charts, components


def render() -> None:
    """Renderiza la vista Salud del Modelo."""
    st.title("Salud del Modelo")
    st.caption(
        "PSI entre la distribución de referencia del corpus y la observada en "
        "producción · Sin raw payload · INV-05"
    )

    baseline = load_corpus_baseline()
    query = PredictionQueryService()
    try:
        metrics = query.metrics()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo leer la evidencia predictiva: {exc}")
        return

    observed_counts = {
        "LOW": metrics.get("low", 0),
        "MEDIUM": metrics.get("medium", 0),
        "HIGH": metrics.get("high", 0),
    }
    observed_total = sum(observed_counts.values())
    if observed_total == 0:
        st.info("Sin predicciones observadas todavía. La deriva no es medible.")
        return

    class_psi_val = psi_from_counts(
        observed_counts,
        {
            k: round(v * baseline["sample_size"])
            for k, v in baseline.get("class_distribution", {}).items()
        },
    )
    sev = severity(class_psi_val)

    st.columns(3)
    components.kpi("PSI de clases (global)", f"{class_psi_val:.4f}")
    components.kpi("Severidad", sev)
    components.kpi(
        "Features comparables",
        len(baseline.get("feature_psi_support", [])),
        caption="Cobertura de features soportadas (no PSI por feature)",
    )

    st.markdown("**Distribución de clases: baseline (corpus) vs observada**")
    dist_df = pd.DataFrame(
        [
            {
                "Clase": k,
                "Baseline (corpus)": baseline.get("class_distribution", {}).get(k, 0),
                "Observada (producción)": round(
                    observed_counts.get(k, 0) / observed_total, 4
                ),
            }
            for k in ("LOW", "MEDIUM", "HIGH")
        ]
    )
    st.echarts_chart(
        charts.deriva_barras(dist_df),
        theme="streamlit",
        renderer="svg",
        key="mh_deriva",
    )

    st.markdown("**Features soportadas**")
    support = baseline.get("feature_psi_support", [])
    if support:
        st.dataframe(
            pd.DataFrame({"feature": support}),
            use_container_width=True,
            height=200,
        )
    else:
        st.info("Sin features soportadas en el baseline.")

    st.markdown("**Últimos reportes de deriva persistidos**")
    store = DriftStore()
    last = store.latest_reports(limit=10)
    if last:
        ldf = pd.DataFrame(last)
        ldf["class_psi"] = ldf["class_psi"].round(4)
        st.dataframe(ldf, use_container_width=True, height=260)
    else:
        st.info(
            "Sin reportes persistidos todavía. La sección muestra el cálculo "
            "en vivo; la persistencia queda registrada al correr el detector."
        )