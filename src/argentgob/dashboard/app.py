"""
Dashboard Streamlit — ArgentGob-Mesh MVP.
Muestra decisiones de gobernanza en tiempo real desde PostgreSQL.

INV-05: nunca muestra execution_arguments ni el payload crudo (solo digest,
acción, reason_code y latencia).
Accesible en: http://localhost:8501
"""
import os
import time

import pandas as pd
import psycopg
import streamlit as st
from psycopg.rows import dict_row

st.set_page_config(
    page_title="ArgentGob-Mesh · Governance Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

REFRESH_SECS = 5  # Auto-refresh cada 5 segundos


def _normalize_db_url(url: str) -> str:
    """Convierte una URL estilo SQLAlchemy a una URL nativa de psycopg3."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


DATABASE_URL = _normalize_db_url(os.environ.get("DATABASE_URL", ""))


def get_connection():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def fetch_decisions(limit: int = 200) -> pd.DataFrame:
    """Lee las últimas decisiones desde governance_decisions (sin raw args)."""
    sql = """
        SELECT
            decision_id,
            agent_id,
            agent_role,
            tool_name,
            action,
            reason_code,
            payload_digest,
            latency_pre_ms,
            decided_at
        FROM governance_decisions
        ORDER BY decided_at DESC
        LIMIT %(limit)s
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"limit": limit})
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def fetch_events(limit: int = 100) -> pd.DataFrame:
    """Lee los últimos eventos de ejecución desde governance_events."""
    sql = """
        SELECT
            id,
            event_id,
            decision_id,
            tool_name,
            status,
            error_message,
            recorded_at
        FROM governance_events
        ORDER BY recorded_at DESC
        LIMIT %(limit)s
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"limit": limit})
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def fetch_summary() -> dict:
    """Estadísticas agregadas de las decisiones registradas."""
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE action = 'PASS')  AS total_pass,
            COUNT(*) FILTER (WHERE action = 'BLOCK') AS total_block,
            COUNT(*)                                  AS total,
            ROUND(AVG(latency_pre_ms)::numeric, 2)    AS avg_pre_ms
        FROM governance_decisions
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return cur.fetchone() or {}


st.title("🛡️ ArgentGob-Mesh · Governance Dashboard")
st.caption(
    f"Actualización automática cada {REFRESH_SECS} s · Sin raw payload · INV-05"
)

try:
    summary = fetch_summary()
    total = summary.get("total", 0) or 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total decisiones", total)
    pass_pct = f"{100 * summary['total_pass'] // total}%" if total else None
    col2.metric("✅ PASS", summary.get("total_pass", 0), delta=pass_pct)
    col3.metric("⛔ BLOCK", summary.get("total_block", 0))
    col4.metric("Latencia pre-hook (avg)", f"{summary.get('avg_pre_ms', '—')} ms")

    st.divider()

    st.subheader("Decisiones recientes")
    df = fetch_decisions()

    if df.empty:
        st.info("Sin decisiones todavía. Iniciá el agente con `make agent`.")
    else:
        def color_action(val):
            return (
                "background-color: #d4edda"
                if val == "PASS"
                else "background-color: #f8d7da"
            )

        styled = df.style.map(color_action, subset=["action"])
        st.dataframe(styled, use_container_width=True, height=300)

    st.divider()

    st.subheader("Eventos de ejecución")
    edf = fetch_events()
    if edf.empty:
        st.info("Sin eventos de ejecución registrados.")
    else:
        st.dataframe(edf, use_container_width=True, height=200)

    if not df.empty:
        st.divider()
        st.subheader("Distribución por herramienta")
        chart_data = (
            df.groupby(["tool_name", "action"])
            .size()
            .reset_index(name="count")
            .pivot(index="tool_name", columns="action", values="count")
            .fillna(0)
        )
        st.bar_chart(chart_data)

except Exception as exc:  # noqa: BLE001 - mostrar error de conexión en la UI
    st.error(f"Error conectando a la base de datos: {exc}")
    st.info(
        "Asegurate de que PostgreSQL esté corriendo: "
        "`podman compose up -d postgres`"
    )

time.sleep(REFRESH_SECS)
st.rerun()
