"""Constructores JSON puros para `st.echarts_chart`.

Sin callbacks JS; formateo vía plantillas string y `valueFormatter`. Los
gráficos reciben datos ya procesados (no hacen consultas propias).

Reglas:
- `renderer="svg"` para impresión fiel (regla 5.5).
- Ejes de conteo con `min: 0` (A10).
- Barras horizontales para categorías con etiquetas largas (A12).
- `selectedMode` va dentro del spec (no como kwarg del widget).
"""
from __future__ import annotations

from argentgob.dashboard import palette as theme


def _base() -> dict:
    return theme.echarts_base()


def _tooltip_value() -> dict:
    return {"trigger": "axis", "axisPointer": {"type": "shadow"}}


def actividad_por_jornada(df) -> dict:
    """Barras apiladas por fecha categórica YYYY-MM-DD (corrige B5)."""
    spec = _base()
    spec.update(
        {
            "tooltip": _tooltip_value(),
            "xAxis": {"type": "category", "data": list(df.index)},
            "yAxis": {"type": "value", "min": 0},
            "series": [
                {
                    "name": col,
                    "type": "bar",
                    "stack": "total",
                    "data": df[col].tolist(),
                    "itemStyle": {"color": theme.estado_color(col)},
                }
                for col in df.columns
            ],
            "legend": {"bottom": 0},
        }
    )
    return spec


def decisiones_por_herramienta(df) -> dict:
    """Barras horizontales apiladas por herramienta (corrige B1, A12)."""
    spec = _base()
    spec.update(
        {
            "tooltip": _tooltip_value(),
            "xAxis": {"type": "value", "min": 0},
            "yAxis": {
                "type": "category",
                "data": list(df.index),
                "inverse": True,
            },
            "series": [
                {
                    "name": col,
                    "type": "bar",
                    "stack": "total",
                    "data": df[col].tolist(),
                    "itemStyle": {"color": theme.estado_color(col)},
                }
                for col in df.columns
            ],
            "legend": {"bottom": 0},
        }
    )
    return spec


def motivos_bloqueo(df) -> dict:
    """Barras horizontales de motivos de bloqueo ordenadas por volumen (B4)."""
    spec = _base()
    spec.update(
        {
            "tooltip": _tooltip_value(),
            "xAxis": {"type": "value", "min": 0},
            "yAxis": {
                "type": "category",
                "data": list(df["reason_code"]),
                "inverse": True,
            },
            "series": [
                {
                    "name": "Bloqueos",
                    "type": "bar",
                    "data": df["count"].tolist(),
                    "itemStyle": {"color": theme.BLOCK},
                    "label": {"show": True, "position": "right"},
                }
            ],
        }
    )
    return spec


def heatmap_prediccion_decision(df) -> dict:
    """Heatmap Predicción × Decisión final (A11)."""
    spec = _base()
    spec.update(
        {
            "tooltip": {"position": "top"},
            "grid": {"left": 8, "right": 8, "top": 8, "bottom": 8, "containLabel": True},
            "xAxis": {"type": "category", "data": list(df.columns)},
            "yAxis": {"type": "category", "data": list(df.index)},
            "visualMap": {"min": 0, "max": int(df.values.max()) if len(df.values) else 1},
            "series": [
                {
                    "name": "Predicción × Decisión",
                    "type": "heatmap",
                    "data": [
                        [j, i, int(df.iloc[i, j])]
                        for i in range(len(df.index))
                        for j in range(len(df.columns))
                    ],
                    "label": {"show": True},
                }
            ],
        }
    )
    return spec


def motivos_divergencia(df) -> dict:
    """Barras horizontales de motivos de divergencia (A11)."""
    spec = _base()
    spec.update(
        {
            "tooltip": _tooltip_value(),
            "xAxis": {"type": "value", "min": 0},
            "yAxis": {
                "type": "category",
                "data": list(df["divergence"]),
                "inverse": True,
            },
            "series": [
                {
                    "name": "Divergencias",
                    "type": "bar",
                    "data": df["count"].tolist(),
                    "itemStyle": {"color": theme.HITL},
                    "label": {"show": True, "position": "right"},
                }
            ],
        }
    )
    return spec


def matriz_confusion(cm, labels) -> dict:
    """Heatmap de matriz de confusión (n=600, valores absolutos)."""
    spec = _base()
    spec.update(
        {
            "tooltip": {"position": "top"},
            "grid": {"left": 8, "right": 8, "top": 8, "bottom": 8, "containLabel": True},
            "xAxis": {"type": "category", "data": labels, "name": "Predicción"},
            "yAxis": {"type": "category", "data": labels, "name": "Real"},
            "visualMap": {"min": 0, "max": int(max(max(r) for r in cm)) if cm else 1},
            "series": [
                {
                    "name": "Matriz de confusión",
                    "type": "heatmap",
                    "data": [
                        [j, i, int(cm[i][j])]
                        for i in range(len(cm))
                        for j in range(len(cm[i]))
                    ],
                    "label": {"show": True},
                }
            ],
        }
    )
    return spec


def curva_calibracion(curve) -> dict:
    """Curva de calibración: mean_predicted vs observed_frequency + y=x."""
    spec = _base()
    spec.update(
        {
            "tooltip": {
                "trigger": "item",
                "formatter": "{b}<br/>Predicho: {c}",
            },
            "xAxis": {"type": "value", "min": 0, "max": 1, "name": "mean_predicted"},
            "yAxis": {"type": "value", "min": 0, "max": 1, "name": "observed_frequency"},
            "series": [
                {
                    "name": "Ideal y=x",
                    "type": "line",
                    "data": [[0, 0], [1, 1]],
                    "lineStyle": {"type": "dashed", "color": theme.UNKNOWN},
                    "symbol": "none",
                },
                {
                    "name": "Calibración",
                    "type": "line",
                    "data": [
                        [r["mean_predicted"], r["observed_frequency"]]
                        for r in curve
                    ],
                    "itemStyle": {"color": theme.INFO},
                    "lineStyle": {"color": theme.INFO},
                },
            ],
        }
    )
    return spec


def deriva_barras(df) -> dict:
    """Barras agrupadas: % Baseline vs % Observada por clase (A11)."""
    spec = _base()
    spec.update(
        {
            "tooltip": _tooltip_value(),
            "xAxis": {"type": "category", "data": list(df["Clase"])},
            "yAxis": {"type": "value", "min": 0, "max": 1, "name": "proporción"},
            "series": [
                {
                    "name": "Baseline (corpus)",
                    "type": "bar",
                    "data": df["Baseline (corpus)"].tolist(),
                    "itemStyle": {"color": theme.UNKNOWN},
                },
                {
                    "name": "Observada (producción)",
                    "type": "bar",
                    "data": df["Observada (producción)"].tolist(),
                    "itemStyle": {"color": theme.INFO},
                },
            ],
            "legend": {"bottom": 0},
        }
    )
    return spec