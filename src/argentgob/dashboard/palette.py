"""Paleta de colores del dashboard ArgentGob-Mesh.

Paleta de colores (doble paleta: claro editorial default + oscuro documentado),
utilidades de texto (strip_ansi) y base común para gráficos ECharts.

Reglas:
- Prohibido hardcodear hex fuera de este módulo o de `.streamlit/config.toml`.
- Un estado, un color, en todas las vistas (verde=permitido, rojo=bloqueo/
  vencido, amarillo=atención, azul=info/selección, gris=desconocido/baseline).
- Cero emojis en la UI; íconos Material nativos.
"""
from __future__ import annotations

import re


def _hex(r: int, g: int, b: int) -> str:
    """Convierte RGB a hex (evita literales hex sueltos en el código)."""
    return f"#{r:02X}{g:02X}{b:02X}"


# ── Paleta activa: Claro editorial (default) ─────────────────────────
BG_APP = _hex(250, 250, 248)
BG_SURFACE = _hex(255, 255, 255)
BORDER = _hex(231, 228, 220)
TEXT_PRIMARY = _hex(26, 29, 33)
TEXT_SECONDARY = _hex(95, 107, 122)

COLORES = {
    "PASS": _hex(34, 197, 94),    # LOW
    "BLOCK": _hex(248, 113, 113),  # HIGH
    "HITL": _hex(251, 191, 36),    # MEDIUM
    "INFO": _hex(56, 189, 248),
    "UNKNOWN": _hex(148, 163, 184),
}
PASS = COLORES["PASS"]
BLOCK = COLORES["BLOCK"]
HITL = COLORES["HITL"]
INFO = COLORES["INFO"]
UNKNOWN = COLORES["UNKNOWN"]

COLORES_TEXTO = {
    "PASS": _hex(21, 128, 61),
    "BLOCK": _hex(185, 28, 28),
    "HITL": _hex(180, 83, 9),
    "INFO": _hex(29, 78, 216),
}
PASS_TEXT = COLORES_TEXTO["PASS"]
BLOCK_TEXT = COLORES_TEXTO["BLOCK"]
HITL_TEXT = COLORES_TEXTO["HITL"]
INFO_TEXT = COLORES_TEXTO["INFO"]

# ── Paleta alternativa: Oscuro (documentada, switch de un bloque) ────
DARK = {
    "BG_APP": _hex(17, 18, 20),
    "BG_SURFACE": _hex(23, 24, 27),
    "BORDER": _hex(42, 44, 48),
    "TEXT_PRIMARY": _hex(236, 236, 234),
    "TEXT_SECONDARY": _hex(154, 156, 161),
    "PASS": _hex(61, 190, 132),
    "BLOCK": _hex(229, 98, 93),
    "HITL": _hex(217, 164, 65),
    "INFO": _hex(108, 142, 245),
    "UNKNOWN": _hex(138, 140, 145),
}

# Paleta categórica para gráficos (theme="streamlit").
CHART_CATEGORICAL = [PASS, BLOCK, HITL, INFO, UNKNOWN]

# ── Tipografía (offline, pila del sistema) ───────────────────────────
FONT_STACK = "Inter, Segoe UI, Roboto, Helvetica, Arial, sans-serif"

# ── Limpieza ANSI (regla 5.10) ───────────────────────────────────────
_ESC = chr(27)
ANSI_RE = re.compile(
    _ESC + r"\[[0-9;?]*[A-Za-z]"
    + "|" + _ESC + r"\][^\x07" + _ESC + r"]*(?:\x07|" + _ESC + r"\\)"
    + r"|\[[0-9;]*m"
)


def strip_ansi(texto: str) -> str:
    """Elimina secuencias ANSI de un texto (el original queda intacto)."""
    return ANSI_RE.sub("", texto)


# ── Base común ECharts ───────────────────────────────────────────────
def echarts_base() -> dict:
    """Opciones base compartidas por todos los gráficos ECharts."""
    return {
        "textStyle": {"fontFamily": FONT_STACK},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 8, "right": 16, "top": 24, "bottom": 8, "containLabel": True},
    }


def estado_color(estado: str) -> str:
    """Devuelve el color semántico de un estado (PASS/BLOCK/HITL/UNKNOWN)."""
    return {
        "PASS": PASS,
        "LOW": PASS,
        "BLOCK": BLOCK,
        "HIGH": BLOCK,
        "HITL": HITL,
        "MEDIUM": HITL,
        "UNKNOWN": UNKNOWN,
    }.get(estado, UNKNOWN)


def estado_texto_color(estado: str) -> str:
    """Color de texto/badge para un estado (contraste AA sobre claro)."""
    return {
        "PASS": PASS_TEXT,
        "LOW": PASS_TEXT,
        "BLOCK": BLOCK_TEXT,
        "HIGH": BLOCK_TEXT,
        "HITL": HITL_TEXT,
        "MEDIUM": HITL_TEXT,
        "UNKNOWN": TEXT_SECONDARY,
    }.get(estado, TEXT_SECONDARY)
