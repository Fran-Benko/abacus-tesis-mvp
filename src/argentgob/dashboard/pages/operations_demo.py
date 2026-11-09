"""Vista 5 · Operaciones y Demo.

Controles de ejecución end-to-end, resultados por escenario en tarjetas limpias
(sin emojis) y consola de logs con `strip_ansi`, `wrap_lines=True`, filtro por
nivel de severidad y búsqueda de texto (A7, A12).
"""
from __future__ import annotations

import streamlit as st

from argentgob.dashboard import demo_runner
from argentgob.dashboard import palette as theme


def _icono_outcome(outcome: str) -> str:
    """Ícono Material según el outcome (sin emojis)."""
    if "HITL" in outcome or "APPROB" in outcome:
        return ":material/schedule:"
    if "BLOCK" in outcome or "BLOQUEADO" in outcome:
        return ":material/block:"
    return ":material/check_circle:"


@st.fragment(run_every="5s")
def _estado_demo():
    """Estado + resultados + consola de logs (bloque vivo, refresco 5s)."""
    status = demo_runner.demo_status_snapshot()
    if status["running"]:
        st.success("Demo en ejecución")
    elif status["done"]:
        if status["returncode"] == 0:
            st.success(f"Demo finalizada (exit {status['returncode']})")
        else:
            st.error(f"Demo finalizó con error (exit {status['returncode']})")
    else:
        st.info("Demo inactiva")

    # Resultados por escenario en tarjetas limpias.
    esc = status["escenarios"]
    if esc:
        st.markdown("**Resultado por escenario**")
        for e in esc:
            st.markdown(
                f"- {_icono_outcome(e['outcome'])} **{e['titulo']}**: "
                f"{e['outcome'] or '—'}"
            )

    # Consola de logs: strip_ansi + wrap + filtro por nivel + búsqueda.
    texto = status["lines"]
    if texto:
        st.markdown("**Consola de logs**")
        nivel = st.selectbox(
            "Nivel", ["todos", "warning", "info", "error"], key="op_nivel"
        )
        busqueda = st.text_input("Buscar en logs", key="op_busqueda")

        lineas = texto.splitlines()
        if nivel != "todos":
            lineas = [l for l in lineas if f"[{nivel}" in l or f" {nivel} " in l]
        if busqueda:
            lineas = [l for l in lineas if busqueda.lower() in l.lower()]

        limpio = theme.strip_ansi("\n".join(lineas))
        st.code(limpio, language="text", wrap_lines=True, line_numbers=True)
    elif status["running"]:
        st.info("El demo está arrancando… los logs aparecen acá.")


def render() -> None:
    """Renderiza la vista Operaciones y Demo."""
    st.title("Operaciones y Demo")
    st.caption(
        "Ejecución end-to-end de los 5 escenarios de gobernanza · "
        "Persiste en PostgreSQL · Sin raw payload · INV-05"
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Correr demo (persistir)", key="op_run", use_container_width=True):
            ok, msg = demo_runner.start_demo()
            st.toast(msg if ok else f"Advertencia: {msg}")
    with c2:
        if st.button("Limpiar log", key="op_clear", use_container_width=True):
            demo_runner.limpiar_log()

    _estado_demo()