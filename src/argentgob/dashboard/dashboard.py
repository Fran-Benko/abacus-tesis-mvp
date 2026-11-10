"""Entrypoint del dashboard ArgentGob-Mesh (Streamlit).

Configura la navegación multipágina (`st.navigation` + `st.Page`) y delega el
renderizado a las vistas en `pages/`. La lógica de negocio, autorizaciones e
INV-05 quedan intactas; solo cambia la presentación.

Accesible en: http://localhost:8501
"""
from __future__ import annotations

import streamlit as st

from argentgob.dashboard.pages import (
    audit_hitl,
    executive_overview,
    live_finance,
    model_health,
    operations_demo,
    predictive_intelligence,
)

st.set_page_config(
    page_title="ArgentGob-Mesh · AI Governance Control Center",
    page_icon=":material/security:",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page(
        executive_overview.render,
        title="Executive Overview",
        icon=":material/dashboard:",
        default=True,
        url_path="overview",
    ),
    st.Page(
        audit_hitl.render,
        title="Auditoría y HITL",
        icon=":material/verified_user:",
        url_path="audit",
    ),
    st.Page(
        predictive_intelligence.render,
        title="Inteligencia Predictiva",
        icon=":material/insights:",
        url_path="predictive",
    ),
    st.Page(
        model_health.render,
        title="Salud del Modelo",
        icon=":material/monitor_heart:",
        url_path="model-health",
    ),
    st.Page(
        operations_demo.render,
        title="Operaciones y Demo",
        icon=":material/terminal:",
        url_path="operations",
    ),
    st.Page(
        live_finance.render,
        title="Finanzas en vivo",
        icon=":material/bolt:",
        url_path="live-finance",
    ),
]

pg = st.navigation(pages, position="sidebar")
pg.run()