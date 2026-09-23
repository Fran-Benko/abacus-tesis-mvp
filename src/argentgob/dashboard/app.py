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

from argentgob.console.hitl_detail import HitlDetailService
from argentgob.console.resolution import ResolutionService
from argentgob.console.service import ConsoleQueryService
from argentgob.core.config import get_settings
from argentgob.hitl.resolver import ApprovalResolver
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole

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


# ── H8: Consola sanitizada y resolución autorizada ──────────────────
def _build_h8_services():
    """Construye los servicios H8 con la configuración actual."""
    settings = get_settings()
    identity = AuditorIdentityProvider(
        environment=settings.environment,
        development_only=settings.identity_development_only,
        session_ttl_seconds=settings.identity_session_ttl_seconds,
        fixture_roles=settings.identity_fixture_roles,
    )
    resolver = ApprovalResolver(settings=settings)
    detail = HitlDetailService()
    query = ConsoleQueryService()
    resolution = ResolutionService(
        identity_provider=identity,
        resolver=resolver,
        detail_service=detail,
    )
    return query, detail, resolution, identity


def _render_h8_console():
    """Renderiza la consola H8: métricas, filtros, detalle y resolución."""
    query, detail, resolution, identity = _build_h8_services()

    # Métricas agregadas.
    metrics = query.metrics()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", metrics["total"])
    c2.metric("✅ PASS", metrics["pass"])
    c3.metric("⛔ BLOCK", metrics["block"])
    c4.metric("🕐 HITL", metrics["hitl"])
    c5.metric("❓ UNKNOWN", metrics["unknown"])

    # Estado de la cadena de auditoría.
    audit = query.audit_status()
    if audit["ok"]:
        st.success("🔗 Cadena de auditoría íntegra")
    else:
        st.error(
            f"🔗 Cadena de auditoría: {audit['reason']} "
            f"(sequence={audit['sequence']})"
        )

    st.divider()

    # Filtros parametrizados.
    st.subheader("Decisiones sanitizadas (filtros parametrizados)")
    with st.expander("Filtros", expanded=False):
        f_action = st.selectbox(
            "Acción", ["", "PASS", "BLOCK", "HITL"], key="h8_f_action"
        )
        f_status = st.selectbox(
            "Estado", ["", "SUCCESS", "FAILED", "UNKNOWN"], key="h8_f_status"
        )
        f_limit = st.slider("Límite", 10, 500, 100, key="h8_f_limit")

    rows = query.list_decisions(
        action=f_action or None,
        status=f_status or None,
        limit=f_limit,
    )
    if not rows:
        st.info("Sin decisiones en la vista sanitizada.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=300)

    st.divider()

    # Approvals PENDING y resolución autorizada.
    st.subheader("HITL · Approvals pendientes")
    pending = query.pending_approvals()
    if not pending:
        st.info("Sin approvals pendientes.")
    else:
        pdf = pd.DataFrame(pending)
        st.dataframe(pdf, use_container_width=True, height=200)

        # Selección de approval para detalle/resolución.
        approval_ids = [r["approval_id"] for r in pending]
        selected = st.selectbox(
            "Approval", approval_ids, key="h8_approval_select"
        )
        if selected:
            try:
                det = detail.get_detail(approval_id=selected)
                st.json(det)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"No se pudo cargar el detalle: {exc}")

            # Resolución autorizada (modo fixture de desarrollo).
            st.markdown("**Resolución autorizada**")
            st.caption(
                "Requiere identidad con rol auditor + evidencia vigente "
                "(modo fixture, development_only)."
            )
            actor = st.text_input(
                "Actor (fixture)", value="auditor-1", key="h8_actor"
            )
            decision = st.selectbox(
                "Decisión", ["APPROVED", "REJECTED"], key="h8_decision"
            )
            comment = st.text_input(
                "Comentario (sanitizado, opcional)", key="h8_comment"
            )
            if st.button("Resolver", key="h8_resolve_btn"):
                try:
                    evidence = identity.authenticate(
                        identifier=actor, role=AppRole.AUDITOR
                    )
                    result = resolution.resolve(
                        approval_id=selected,
                        decision=decision,
                        evidence=evidence,
                        comment=comment or None,
                    )
                    st.json(result)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Error resolviendo: {exc}")


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

        # ── H8: Consola sanitizada y resolución autorizada ──────────────
        st.divider()
        st.header("🛡️ H8 · Consola sanitizada y resolución autorizada")
        st.caption(
            "Vista sanitizada (sin raw payload) · Filtros parametrizados · "
            "Detalle HITL · Resolución con identidad autorizada"
        )

        _render_h8_console()

except Exception as exc:  # noqa: BLE001 - mostrar error de conexión en la UI
    st.error(f"Error conectando a la base de datos: {exc}")
    st.info(
        "Asegurate de que PostgreSQL esté corriendo: "
        "`podman compose up -d postgres`"
    )

time.sleep(REFRESH_SECS)
st.rerun()