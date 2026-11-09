"""Vista 2 · Auditoría y HITL.

Consola sanitizada, filtros parametrizados persistentes, cola de aprobaciones
(vigentes/vencidas separadas) y resolución autorizada en `st.dialog`. La lógica
de autorización (`identity.authenticate` + `resolution.resolve`) queda intacta.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from argentgob.console.hitl_detail import HitlDetailService
from argentgob.console.resolution import ResolutionService
from argentgob.console.service import ConsoleQueryService
from argentgob.core.config import get_settings
from argentgob.dashboard import components
from argentgob.hitl.resolver import ApprovalResolver
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole


def _build_services():
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


@st.dialog("Resolución autorizada", width="small")
def _dialogo_resolucion(approval_id: str, detail, resolution, identity):
    """Modal de resolución con confirmación explícita (lógica intacta)."""
    st.caption(f"Approval: `{approval_id}`")
    actor = st.text_input("Actor (fixture)", value="auditor-1", key="aud_actor")
    decision = st.selectbox(
        "Decisión", ["APPROVED", "REJECTED"], key="aud_decision"
    )
    comment = st.text_input(
        "Comentario (sanitizado, opcional)", key="aud_comment"
    )
    if st.button("Confirmar resolución", key="aud_confirm"):
        try:
            evidence = identity.authenticate(
                identifier=actor, role=AppRole.AUDITOR
            )
            result = resolution.resolve(
                approval_id=approval_id,
                decision=decision,
                evidence=evidence,
                comment=comment or None,
            )
            st.success("Resolución registrada")
            st.json(result)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Error resolviendo: {exc}")


def render() -> None:
    """Renderiza la vista Auditoría y HITL."""
    query, detail, resolution, identity = _build_services()

    st.title("Auditoría y HITL")
    st.caption(
        "Consola sanitizada (sin raw payload) · Filtros parametrizados · "
        "Resolución con identidad autorizada"
    )

    # Métricas del universo de consola sanitizada (alcance rotulado, A6).
    metrics = query.metrics()
    st.columns(5)
    components.kpi("Total (consola)", metrics["total"], caption="Universo consola H8")
    components.kpi("PASS", metrics["pass"])
    components.kpi("BLOCK", metrics["block"])
    components.kpi("HITL", metrics["hitl"])
    components.kpi("UNKNOWN", metrics["unknown"])

    audit = query.audit_status()
    if audit["ok"]:
        st.success("Cadena de auditoría íntegra")
    else:
        st.error(f"Cadena de auditoría: {audit['reason']}")

    st.divider()

    # Filtros persistentes (B3: no se pierden entre refrescos).
    st.subheader("Decisiones sanitizadas")
    with st.expander("Filtros", expanded=False):
        f_action = st.selectbox(
            "Acción", ["", "PASS", "BLOCK", "HITL"], key="aud_f_action"
        )
        f_status = st.selectbox(
            "Estado", ["", "SUCCESS", "FAILED", "UNKNOWN"], key="aud_f_status"
        )
        f_tool = st.text_input("Herramienta", key="aud_f_tool")
        f_limit = st.slider("Límite", 10, 500, 100, key="aud_f_limit")

    rows = query.list_decisions(
        action=f_action or None,
        status=f_status or None,
        tool_name=f_tool or None,
        limit=f_limit,
    )
    if not rows:
        st.info("Sin decisiones en la vista sanitizada.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=300)

    st.divider()

    # Cola de aprobaciones: vigentes vs vencidas (A3).
    st.subheader("Aprobaciones HITL")
    vigentes = query.pending_approvals(limit=100)
    vencidas = query.expired_approvals(limit=100)

    if vigentes:
        st.markdown(f"**{len(vigentes)} pendientes vigentes**")
        vdf = pd.DataFrame(vigentes)
        st.dataframe(vdf, use_container_width=True, height=180)
    else:
        st.info("Sin aprobaciones pendientes vigentes.")

    if vencidas:
        st.markdown(f"**{len(vencidas)} vencidas (requieren atención)**")
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

    # Selección de approval para detalle y resolución.
    todos = vigentes + vencidas
    if todos:
        approval_ids = [a["approval_id"] for a in todos]
        selected = st.selectbox("Approval", approval_ids, key="aud_approval")
        if selected:
            try:
                det = detail.get_detail(approval_id=selected)
                st.markdown("**Detalle sanitizado**")
                st.dataframe(
                    pd.DataFrame([det]).T.reset_index().rename(columns={"index": "Campo", 0: "Valor"}),
                    use_container_width=True,
                    height=220,
                )
            except Exception as exc:  # noqa: BLE001
                st.warning(f"No se pudo cargar el detalle: {exc}")

            if st.button("Resolver", key="aud_resolve_btn"):
                _dialogo_resolucion(selected, detail, resolution, identity)