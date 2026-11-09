"""Componentes reutilizables del dashboard ArgentGob-Mesh.

KPIs con proporciones neutras (sin flechas falsas), badges semánticos,
cabecera con estado del sistema y helpers de formato. Cero emojis.
"""
from __future__ import annotations

import streamlit as st


def kpi(
    label: str,
    value,
    *,
    caption: str | None = None,
    icon: str | None = None,
    border: bool = True,
) -> None:
    """Tarjeta KPI con proporción neutra (regla 5.1: sin delta engañoso)."""
    st.metric(
        label,
        value,
        delta=None,
        delta_arrow="off",
        icon=icon,
        border=border,
        help=caption,
    )


def badge_estado(estado: str) -> None:
    """Badge semántico de estado (PASS/BLOCK/HITL/UNKNOWN)."""
    color = {
        "PASS": "green",
        "LOW": "green",
        "BLOCK": "red",
        "HIGH": "red",
        "HITL": "yellow",
        "MEDIUM": "yellow",
        "UNKNOWN": "gray",
    }.get(estado, "gray")
    st.badge(estado, color=color)


def edad_humana(segundos: float) -> str:
    """Formatea una edad en segundos a formato humano (46d 19h, 5m 12s)."""
    segundos = max(0, int(segundos))
    dias, resto = divmod(segundos, 86400)
    horas, resto = divmod(resto, 3600)
    minutos, segs = divmod(resto, 60)
    if dias:
        return f"{dias}d {horas}h"
    if horas:
        return f"{horas}h {minutos}m"
    if minutos:
        return f"{minutos}m {segs}s"
    return f"{segs}s"


def estado_sistema(audit_ok: bool, hay_vencidas: bool, db_ok: bool) -> str:
    """Estado global del sistema desde condiciones reales (regla 6.4)."""
    if not db_ok:
        return "Base de datos no disponible"
    if not audit_ok:
        return "Fallo en cadena de auditoría"
    if hay_vencidas:
        return "Aprobaciones vencidas sin resolver"
    return "Operativo"