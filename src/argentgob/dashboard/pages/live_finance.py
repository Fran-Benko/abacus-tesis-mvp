"""Vista 6 · Finanzas en vivo (AV-03).

Sección nueva del dashboard que opera la API interna de runs sin sustituir la
demo determinista de Operaciones y Demo.

Reglas de la unidad AV-03:

- El operador elige actor y perfil de un catálogo cerrado; `admin` no existe en
  la UI (AV-I05) y el campo financiero está acotado por el esquema de la API.
- El `POST /runs` ocurre **solo** por intención explícita (botón). El sondeo
  posterior vive en un `st.fragment` con `run_every`, de modo que no bloquea el
  resto de la página ni reenvía la admisión.
- El timeline separa herramienta, decisión, ejecución, contenido y HITL
  pendiente; el informe se muestra saneado y la evidencia se enlaza por IDs
  seguros (run/tentativa/decisión), nunca por payload.
- API caída, recarga, dos sesiones y truncado se informan sin excepciones y sin
  simular recuperación.
"""
from __future__ import annotations

import streamlit as st

from argentgob.dashboard import components, live_client, live_session
from argentgob.dashboard import palette as theme

# Catálogo cerrado de perfiles operables desde la UI (sin `admin`).
PROFILES: tuple[str, ...] = ("analyst", "analyst_restricted")
ACTORS: tuple[str, ...] = ("operador-1", "operador-2")
POLL_SECONDS = "2s"
SESSION_KEY = "live_finance_session"

_ESTADO_ICON = {
    "SUCCEEDED": ":material/check_circle:",
    "PARTIAL": ":material/incomplete:",
    "BLOCKED": ":material/block:",
    "WAITING_APPROVAL": ":material/schedule:",
    "RESUMING": ":material/sync:",
    "FAILED": ":material/error:",
    "TIMED_OUT": ":material/timer_off:",
    "INDETERMINATE": ":material/help:",
}


def _client() -> live_client.LiveApiClient:
    """Cliente por sesión de Streamlit (cacheado por proceso del dashboard)."""
    return live_client.default_client()


def _session() -> live_session.LiveSession | None:
    return st.session_state.get(SESSION_KEY)


def _set_session(session: live_session.LiveSession) -> None:
    st.session_state[SESSION_KEY] = session


def _render_ready(client: live_client.LiveApiClient) -> None:
    """Estado del stack según `/ready`; nunca bloquea el resto de la vista."""
    try:
        ready = client.ready()
    except live_client.LiveApiError as exc:
        st.warning(f"Servicio de runs: {exc.message}")
        return
    checks = ready.get("checks") or {}
    caps = ready.get("capabilities") or {}
    ok = bool(ready.get("ready"))
    if ok:
        st.success("Servicio de runs listo")
    else:
        faltan = [name for name, value in checks.items() if not value]
        st.error(
            "Servicio de runs no listo"
            + (f" · faltan: {', '.join(faltan)}" if faltan else "")
        )
    cols = st.columns(4)
    with cols[0]:
        components.kpi("Esquema en head", "sí" if checks.get("schema_en_head") else "no")
    with cols[1]:
        components.kpi(
            "Catálogo sembrado", "sí" if checks.get("catalogo_sembrado") else "no"
        )
    with cols[2]:
        components.kpi("LLM alcanzable", "sí" if caps.get("llm_reachable") else "no")
    with cols[3]:
        components.kpi("Cupo libre", "sí" if caps.get("capacity_free") else "no")


def _render_form(client: live_client.LiveApiClient) -> None:
    """Intención explícita: actor, perfil y activo acotado."""
    with st.form("live_finance_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            actor = st.selectbox("Actor", ACTORS, key="lf_actor")
            profile = st.selectbox("Perfil", PROFILES, key="lf_profile")
        with c2:
            asset = st.text_input(
                "Activo (ticker)", value="AAPL", max_chars=20, key="lf_asset"
            )
            request_text = st.text_input(
                "Consulta acotada (opcional)",
                max_chars=200,
                key="lf_request",
                help="Se envía como intención; no se persiste ni se muestra cruda.",
            )
        submitted = st.form_submit_button("Iniciar análisis en vivo")

    if not submitted:
        return

    asset_clean = (asset or "").strip().upper()
    if not asset_clean:
        st.error("Indicá un activo.")
        return

    candidate = live_session.LiveSession(
        actor=actor,
        profile=profile,
        asset=asset_clean,
        request_text=(request_text or "").strip() or None,
    )
    current = _session()
    if current is not None and current.idempotency_key == candidate.idempotency_key:
        # Misma intención ya admitida: no se reenvía el POST (T-B02).
        st.info("Esa intención ya tiene un run en esta sesión; se continúa el sondeo.")
        return

    _set_session(live_session.start(candidate, client))
    if candidate.error:
        st.error(candidate.error)


@st.fragment(run_every=POLL_SECONDS)
def _render_live_region() -> None:
    """Región activa: sondeo incremental del run (no refresca toda la página)."""
    session = _session()
    if session is None:
        st.info("Sin run activo. Iniciá un análisis para ver el timeline.")
        return

    if session.run_id and not session.terminal:
        _set_session(live_session.poll(session, _client()))
        session = _session()

    assert session is not None
    _render_status(session)
    _render_timeline(session)
    _render_result(session)


def _render_status(session: live_session.LiveSession) -> None:
    """Cabecera del run: estado, IDs seguros y controles de sesión."""
    icon = _ESTADO_ICON.get(session.state, ":material/pending:")
    st.markdown(f"### {icon} Estado: `{session.state or 'sin iniciar'}`")

    cols = st.columns(4)
    with cols[0]:
        components.kpi("Run", (session.run_id or "—")[:8])
    with cols[1]:
        components.kpi("Eventos", len(session.events))
    with cols[2]:
        components.kpi("Cursor", session.cursor)
    with cols[3]:
        components.kpi("Sondeos", session.polls)

    st.caption(
        f"Actor `{session.actor}` · perfil `{session.profile}` · "
        f"activo `{session.asset}` · intención `{session.idempotency_key[:12]}…`"
    )

    if session.api_unavailable:
        st.warning(
            f"{session.error or 'API no disponible.'} El run no se reenvía: "
            "al restablecerse el servicio el sondeo continúa con el mismo run."
        )
    elif session.error:
        st.error(session.error)

    if session.truncated:
        st.info(
            "El anillo de eventos descartó los más antiguos "
            f"(desde seq {session.first_available_seq}). El estado del run sigue completo."
        )

    note = session.partiality_note()
    if note:
        st.warning(note)

    if session.terminal:
        st.caption("Run en estado terminal: el sondeo está detenido.")
    elif session.waiting_approval:
        st.info(
            "El run espera una aprobación humana. La UI no aprueba: la resolución "
            "se hace en Auditoría y HITL con identidad autorizada."
        )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Nueva sesión", key="lf_reset", use_container_width=True):
            st.session_state.pop(SESSION_KEY, None)
            st.rerun()
    with c2:
        if st.button("Sondear ahora", key="lf_poll", use_container_width=True):
            _set_session(live_session.poll(session, _client()))
            st.rerun()


def _render_timeline(session: live_session.LiveSession) -> None:
    """Timeline por categoría, sin argumentos operativos ni consola raw."""
    st.subheader("Timeline")
    rows = session.timeline()
    if not rows:
        st.info("Todavía no hay eventos publicados para este run.")
        return

    categorias = sorted({row["categoria"] for row in rows})
    seleccion = st.multiselect(
        "Categorías", categorias, default=categorias, key="lf_categorias"
    )
    visibles = [row for row in rows if row["categoria"] in seleccion]
    st.dataframe(
        visibles,
        use_container_width=True,
        height=280,
        column_config={
            "seq": st.column_config.NumberColumn("seq", width="small"),
            "timestamp": st.column_config.TextColumn("timestamp", width="medium"),
            "categoria": st.column_config.TextColumn("categoría", width="small"),
            "evento": st.column_config.TextColumn("evento", width="medium"),
            "herramienta": st.column_config.TextColumn("herramienta", width="small"),
            "accion": st.column_config.TextColumn("acción", width="small"),
            "estado": st.column_config.TextColumn("estado", width="small"),
            "detalle": st.column_config.TextColumn("detalle", width="large"),
        },
    )

    pendientes = session.pending_approvals()
    if pendientes:
        st.markdown("**Aprobaciones pendientes (no aprobadas por la UI)**")
        st.dataframe(pendientes, use_container_width=True, height=140)


def _render_result(session: live_session.LiveSession) -> None:
    """Informe saneado + evidencia por IDs seguros."""
    st.subheader("Informe")
    if not session.result:
        st.caption("El informe aparece cuando el run termina con resultado válido.")
        return

    summary = session.result.get("summary")
    if summary:
        st.markdown(theme.strip_ansi(str(summary)))
    limitaciones = session.result.get("limitations") or []
    if limitaciones:
        st.caption("Limitaciones: " + " · ".join(str(x) for x in limitaciones))
    if session.result.get("truncated"):
        st.info("El informe fue acotado por tamaño; no se publica el texto completo.")

    st.markdown("**Evidencia**")
    st.caption(
        "Referencias seguras para la consola de auditoría: run, tentativas y "
        "decisiones. No se muestran argumentos ni payload."
    )
    evidencia = [
        {
            "tipo": "run",
            "referencia": session.run_id or "—",
            "estado": session.state,
        }
    ]
    for event in session.events:
        if event.get("decision_id"):
            evidencia.append(
                {
                    "tipo": "decisión",
                    "referencia": event["decision_id"],
                    "estado": event.get("action") or "—",
                }
            )
        if event.get("event_id"):
            evidencia.append(
                {
                    "tipo": "tentativa",
                    "referencia": event["event_id"],
                    "estado": event.get("execution_status") or "—",
                }
            )
    st.dataframe(evidencia, use_container_width=True, height=180)


def render() -> None:
    """Renderiza la vista Finanzas en vivo."""
    st.title("Finanzas en vivo")
    st.caption(
        "Análisis financiero con LLM local a través de la API interna de runs · "
        "Eventos por allowlist · Sin payload crudo · INV-05"
    )

    client = _client()
    _render_ready(client)
    st.divider()
    _render_form(client)
    st.divider()
    _render_live_region()
