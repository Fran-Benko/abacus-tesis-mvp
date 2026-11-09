"""Vista 1 · Executive Overview — 3 bloques, máximo 4 visualizaciones.

Estructura: estado → contención → acción requerida. Sin tablas de logs ni
volcados JSON. Los KPIs y la cola HITL viven en `@st.fragment(run_every="5s")`
con toggle de pausa; los gráficos históricos quedan fuera del ciclo (B3).
"""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from argentgob.console.service import ConsoleQueryService
from argentgob.core.config import get_settings
from argentgob.dashboard import charts, components, demo_runner, queries


def _frase_resumen(summary: dict, vencidas: int) -> str:
    """Frase-resumen generada por reglas sobre datos reales (sin especulación)."""
    total = summary.get("total", 0) or 0
    block = summary.get("total_block", 0) or 0
    if total:
        pct = 100 * block / total
        base = f"{block} de {total} decisiones fueron bloqueadas ({pct:.1f}%)"
    else:
        base = "Sin decisiones registradas"
    if vencidas:
        return f"{base}; existen {vencidas} aprobaciones HITL vencidas sin resolver"
    return base


@st.fragment(run_every="5s")
def _kpis_vivos():
    """KPIs operativos + cola HITL (bloque vivo, refresco cada 5s)."""
    summary = queries.fetch_summary()
    total = summary.get("total", 0) or 0

    def _pct(n):
        return f"{100 * n / total:.1f}%" if total else "—"

    st.columns(5)
    components.kpi("Total decisiones", total, caption="Universo completo")
    components.kpi(
        "PASS",
        summary.get("total_pass", 0),
        caption=f"{_pct(summary.get('total_pass', 0))} de {total}",
    )
    components.kpi(
        "BLOCK",
        summary.get("total_block", 0),
        caption=f"{_pct(summary.get('total_block', 0))} de {total}",
    )
    components.kpi(
        "HITL / Otras",
        summary.get("total_hitl", 0) + summary.get("total_other", 0),
        caption=f"{_pct(summary.get('total_hitl', 0) + summary.get('total_other', 0))} de {total}",
    )
    components.kpi(
        "Latencia pre-hook (promedio)",
        f"{summary.get('avg_pre_ms', '—')} ms",
        caption="Promedio aritmético",
    )


@st.fragment(run_every="5s")
def _accion_requerida():
    """Bloque 3 · Acción requerida (único con énfasis visual)."""
    query = ConsoleQueryService()
    audit = query.audit_status()
    vencidas = query.expired_approvals(limit=100)
    vigentes = query.pending_approvals(limit=100)

    st.subheader("Acción requerida")
    c1, c2 = st.columns(2)
    with c1:
        if audit["ok"]:
            st.success("Cadena de auditoría íntegra")
        else:
            st.error(f"Cadena de auditoría: {audit['reason']}")
    with c2:
        if vencidas:
            st.error(f"{len(vencidas)} aprobaciones HITL vencidas sin resolver")
        else:
            st.success("Sin aprobaciones vencidas")

    if vencidas:
        st.markdown("**Aprobaciones vencidas**")
        rows = []
        for a in vencidas:
            edad = components.edad_humana(
                (pd.Timestamp.now("UTC") - pd.Timestamp(a["expires_at"])).total_seconds()
            )
            rows.append(
                {
                    "Approval": a["approval_id"],
                    "Herramienta": a["tool_name"] or "Sin herramienta",
                    "Vencida hace": edad,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=180)
    elif vigentes:
        st.markdown(f"**{len(vigentes)} aprobaciones pendientes vigentes**")


def render() -> None:
    """Renderiza el Executive Overview."""
    settings = get_settings()
    query = ConsoleQueryService()
    vencidas = query.expired_approvals(limit=100)
    summary = queries.fetch_summary()

    # Cabecera
    st.title("ArgentGob-Mesh · AI Governance Control Center")
    h1, h2, h3, h4 = st.columns([2, 1, 1, 1])
    with h1:
        st.caption(f"Entorno: {settings.environment}")
    with h2:
        st.caption(f"Última actualización: {time.strftime('%H:%M:%S')}")
    with h3:
        if st.button("Pausar refresh", key="ov_pause"):
            st.session_state["ov_paused"] = not st.session_state.get("ov_paused", False)
        st.caption(
            "Pausado" if st.session_state.get("ov_paused") else "Refresco activo"
        )
    with h4:
        if settings.environment == "LOCAL":
            with st.popover("Simular tráfico E2E", icon=":material/play_arrow:"):
                st.caption("Ejecuta la demo end-to-end (persiste en PostgreSQL).")
                if st.button("Correr demo", key="ov_demo"):
                    ok, msg = demo_runner.start_demo()
                    st.toast(msg if ok else f"⚠️ {msg}")

    st.markdown(f"**{_frase_resumen(summary, len(vencidas))}**")

    # Bloque 1 · Salud y actividad
    st.subheader("Salud y actividad")
    _kpis_vivos()
    act = queries.actividad_por_jornada()
    if not act.empty:
        st.echarts_chart(
            charts.actividad_por_jornada(act),
            theme="streamlit",
            renderer="svg",
            key="ov_actividad",
        )
    else:
        st.info("Sin decisiones registradas.")

    # Bloque 2 · Contención
    st.subheader("Contención")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Decisiones por herramienta**")
        por_herramienta = queries.decisiones_por_herramienta()
        if not por_herramienta.empty:
            st.echarts_chart(
                charts.decisiones_por_herramienta(por_herramienta),
                theme="streamlit",
                renderer="svg",
                key="ov_herramienta",
            )
        else:
            st.info("Sin datos.")
    with c2:
        st.markdown("**Motivos de bloqueo**")
        motivos = queries.motivos_bloqueo()
        if not motivos.empty:
            st.echarts_chart(
                charts.motivos_bloqueo(motivos),
                theme="streamlit",
                renderer="svg",
                key="ov_motivos",
            )
        else:
            st.info("Sin bloqueos.")

    # Bloque 3 · Acción requerida
    _accion_requerida()