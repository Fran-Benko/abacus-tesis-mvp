"""Tests del design system del dashboard (theme, charts, components, queries).

Verifican funciones puras sin tocar la base de datos:
- `theme.strip_ansi` limpia secuencias ANSI (A7).
- `theme.estado_color`/`estado_texto_color` mapean estados a colores.
- `charts.*` producen specs ECharts válidos con `min: 0` en ejes de conteo.
- `components.edad_humana` formatea edades.
- `queries._saneo_nulos` reemplaza None por etiquetas explícitas (A5).
"""
from __future__ import annotations

import pandas as pd

from argentgob.dashboard import charts, components, queries, theme


# ── theme.strip_ansi (A7) ────────────────────────────────────────────
def test_strip_ansi_limpia_csi():
    texto = "\x1b[2m2026-11-08T04:05:40Z\x1b[0m [\x1b[33m\x1b[1mwarning\x1b[0m]"
    limpio = theme.strip_ansi(texto)
    assert "\x1b" not in limpio
    assert "2026-11-08T04:05:40Z" in limpio
    assert "warning" in limpio


def test_strip_ansi_limpia_osc():
    texto = "\x1b]0;titulo\x07resto"
    limpio = theme.strip_ansi(texto)
    assert "\x1b" not in limpio
    assert "resto" in limpio


def test_strip_ansi_limpia_fragmentos_huerfanos():
    texto = "[33m[1mwarning[0m"
    limpio = theme.strip_ansi(texto)
    assert "warning" in limpio
    assert "[33m" not in limpio


def test_strip_ansi_deja_texto_plano_intacto():
    texto = "ESCENARIO: Consulta benigna (PASS)"
    assert theme.strip_ansi(texto) == texto


# ── theme.estado_color ───────────────────────────────────────────────
def test_estado_color_mapea_estados():
    assert theme.estado_color("PASS") == theme.PASS
    assert theme.estado_color("BLOCK") == theme.BLOCK
    assert theme.estado_color("HITL") == theme.HITL
    assert theme.estado_color("UNKNOWN") == theme.UNKNOWN
    assert theme.estado_color("LOW") == theme.PASS
    assert theme.estado_color("HIGH") == theme.BLOCK
    assert theme.estado_color("MEDIUM") == theme.HITL


def test_estado_color_desconocido_usa_gris():
    assert theme.estado_color("FOO") == theme.UNKNOWN


# ── charts: ejes de conteo con min 0 (A10) ───────────────────────────
def test_actividad_por_jornada_eje_min_0():
    df = pd.DataFrame({"PASS": [1], "BLOCK": [2]}, index=["2026-11-08"])
    spec = charts.actividad_por_jornada(df)
    assert spec["yAxis"]["min"] == 0
    assert spec["xAxis"]["type"] == "category"


def test_decisiones_por_herramienta_eje_min_0():
    df = pd.DataFrame({"PASS": [1], "BLOCK": [2]}, index=["news"])
    spec = charts.decisiones_por_herramienta(df)
    assert spec["xAxis"]["min"] == 0
    assert spec["yAxis"]["type"] == "category"


def test_motivos_bloqueo_eje_min_0():
    df = pd.DataFrame({"reason_code": ["A"], "count": [5]})
    spec = charts.motivos_bloqueo(df)
    assert spec["xAxis"]["min"] == 0


def test_matriz_confusion_genera_heatmap():
    cm = [[299, 1, 0], [0, 180, 0], [0, 0, 120]]
    spec = charts.matriz_confusion(cm, ["LOW", "MEDIUM", "HIGH"])
    assert spec["series"][0]["type"] == "heatmap"
    assert len(spec["series"][0]["data"]) == 9


def test_curva_calibracion_incluye_diagonal():
    curve = [{"mean_predicted": 0.5, "observed_frequency": 0.6}]
    spec = charts.curva_calibracion(curve)
    assert len(spec["series"]) == 2
    assert spec["series"][0]["name"] == "Ideal y=x"


# ── components.edad_humana ───────────────────────────────────────────
def test_edad_humana_dias():
    assert components.edad_humana(46 * 86400 + 19 * 3600) == "46d 19h"


def test_edad_humana_minutos():
    assert components.edad_humana(5 * 60 + 12) == "5m 12s"


def test_edad_humana_segundos():
    assert components.edad_humana(7) == "7s"


def test_edad_humana_negativo():
    assert components.edad_humana(-10) == "0s"


# ── queries._saneo_nulos (A5) ────────────────────────────────────────
def test_saneo_nulos_reemplaza_none():
    df = pd.DataFrame(
        {
            "tool_name": [None, "news"],
            "agent_role": [None, "analyst"],
            "final_action": [None, "PASS"],
        }
    )
    out = queries._saneo_nulos(df)
    assert out["tool_name"].tolist() == ["Sin herramienta", "news"]
    assert out["agent_role"].tolist() == ["No especificado", "analyst"]
    assert out["final_action"].tolist() == ["Sin decisión vinculada", "PASS"]


def test_saneo_nulos_no_toca_columnas_ausentes():
    df = pd.DataFrame({"tool_name": ["news"]})
    out = queries._saneo_nulos(df)
    assert "agent_role" not in out.columns
    assert out["tool_name"].tolist() == ["news"]