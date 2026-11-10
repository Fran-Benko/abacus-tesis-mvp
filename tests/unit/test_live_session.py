"""Tests de la sesión de UI en vivo (AV-03, lógica pura).

Cubren los oráculos T-B02 (idempotencia/rerun), T-B05 (cursor y orden),
T-B07 (polling cesa en terminal), T-B09 (API caída, estados y parcialidad) y
T-P05 (detalle saneado, sin argumentos operativos).
"""
from __future__ import annotations

import pytest

from argentgob.dashboard import live_session
from argentgob.dashboard.live_client import LiveApiError


class _FakeClient:
    """Cliente falso que registra llamadas y devuelve snapshots preparados."""

    def __init__(self, snapshots: list[dict] | None = None, *, fail: bool = False):
        self.snapshots = list(snapshots or [])
        self.fail = fail
        self.created: list[dict] = []
        self.queries: list[dict] = []

    def create_run(self, **kwargs):
        if self.fail:
            raise LiveApiError(None, "API no disponible. Verificá el servicio y reintentá.")
        self.created.append(kwargs)
        return {"run_id": "run-abc", "state": "STARTING", "poll_after_ms": 1000}

    def get_run(self, run_id, *, actor, after_seq=0, limit=100):
        if self.fail:
            raise LiveApiError(None, "API no disponible. Verificá el servicio y reintentá.")
        self.queries.append({"run_id": run_id, "actor": actor, "after_seq": after_seq})
        if self.snapshots:
            return self.snapshots.pop(0)
        return {
            "run_id": run_id,
            "state": "RUNNING",
            "events": [],
            "next_seq": after_seq,
            "first_available_seq": 1,
            "truncated": False,
            "result": None,
        }


def _session(**kwargs) -> live_session.LiveSession:
    base = {"actor": "operador-1", "profile": "analyst", "asset": "AAPL"}
    base.update(kwargs)
    return live_session.LiveSession(**base)


def test_idempotency_key_es_determinista_por_intencion():
    """Misma intención → misma clave; distinto activo → otra clave (T-B02)."""
    a = live_session.build_idempotency_key(
        actor="operador-1", scenario_id="finanzas", profile="analyst",
        asset="aapl", request_text=None,
    )
    b = live_session.build_idempotency_key(
        actor="operador-1", scenario_id="finanzas", profile="analyst",
        asset="AAPL ", request_text=None,
    )
    c = live_session.build_idempotency_key(
        actor="operador-1", scenario_id="finanzas", profile="analyst",
        asset="MSFT", request_text=None,
    )
    assert a == b
    assert a != c
    assert a.startswith("ui-")


def test_doble_clic_no_reenvia_post():
    """Un segundo `start` sobre la misma sesión no crea otro run (T-B02)."""
    client = _FakeClient()
    session = _session()
    live_session.start(session, client)
    live_session.start(session, client)
    assert len(client.created) == 1
    assert session.run_id == "run-abc"


def test_rerun_reconstruye_misma_clave_y_no_duplica():
    """Un rerun del script reconstruye la sesión con la misma clave (T-B02)."""
    client = _FakeClient()
    first = _session()
    live_session.start(first, client)
    second = _session()
    assert second.idempotency_key == first.idempotency_key
    live_session.start(second, client)
    assert client.created[0]["idempotency_key"] == client.created[1]["idempotency_key"]


def test_cursor_avanza_y_no_duplica_eventos():
    """El cursor sigue `next_seq` y los eventos repetidos se descartan (T-B05)."""
    snapshot = {
        "run_id": "run-abc",
        "state": "RUNNING",
        "events": [
            {"seq": 1, "kind": "run.accepted"},
            {"seq": 2, "kind": "tool.requested", "tool": "market_data"},
        ],
        "next_seq": 2,
        "first_available_seq": 1,
        "truncated": False,
        "result": None,
    }
    client = _FakeClient([snapshot, snapshot])
    session = _session()
    live_session.start(session, client)
    live_session.poll(session, client)
    live_session.poll(session, client)
    assert session.cursor == 2
    assert [e["seq"] for e in session.events] == [1, 2]
    assert client.queries[1]["after_seq"] == 2


def test_polling_cesa_en_estado_terminal():
    """En estado terminal no se consulta más la API (T-B07)."""
    client = _FakeClient(
        [
            {
                "run_id": "run-abc",
                "state": "SUCCEEDED",
                "events": [{"seq": 1, "kind": "run.finished", "state": "SUCCEEDED"}],
                "next_seq": 1,
                "first_available_seq": 1,
                "truncated": False,
                "result": {"summary": "informe"},
            }
        ]
    )
    session = _session()
    live_session.start(session, client)
    live_session.poll(session, client)
    assert session.terminal
    live_session.poll(session, client)
    assert len(client.queries) == 1


def test_api_caida_no_borra_la_sesion_ni_reenvia_post():
    """Un fallo de transporte conserva run_id y no repite la admisión (T-B09)."""
    client = _FakeClient()
    session = _session()
    live_session.start(session, client)
    client.fail = True
    live_session.poll(session, client)
    assert session.api_unavailable
    assert session.run_id == "run-abc"
    assert session.error
    live_session.start(session, client)
    assert len(client.created) == 1


def test_error_de_admision_es_seguro():
    """Un 503 se traduce a mensaje seguro sin traza ni payload (T-P05)."""
    client = _FakeClient(fail=True)
    session = _session()
    live_session.start(session, client)
    assert session.run_id is None
    assert session.error == "API no disponible. Verificá el servicio y reintentá."
    assert "Traceback" not in session.error


def test_truncado_se_informa_sin_perder_estado():
    """El truncado del anillo se expone y el estado sigue disponible (T-B07)."""
    client = _FakeClient(
        [
            {
                "run_id": "run-abc",
                "state": "RUNNING",
                "events": [{"seq": 40, "kind": "tool.started", "tool": "market_data"}],
                "next_seq": 40,
                "first_available_seq": 40,
                "truncated": True,
                "result": None,
            }
        ]
    )
    session = _session()
    live_session.start(session, client)
    live_session.poll(session, client)
    assert session.truncated
    assert session.first_available_seq == 40
    assert session.state == "RUNNING"


def test_timeline_clasifica_categorias_y_no_expone_argumentos():
    """Cada `kind` cae en su categoría y el detalle no incluye argumentos (T-P05)."""
    session = _session()
    session.events = [
        {"seq": 1, "kind": "tool.requested", "tool": "market_data"},
        {"seq": 2, "kind": "governance.decided", "action": "PASS", "reason_codes": ["OK"]},
        {"seq": 3, "kind": "tool.completed", "execution_status": "SUCCESS"},
        {"seq": 4, "kind": "content.released", "content_status": "RELEASED"},
        {"seq": 5, "kind": "approval.pending", "approval_ref": "apr-1"},
    ]
    rows = session.timeline()
    assert [r["categoria"] for r in rows] == [
        live_session.CATEGORY_TOOL,
        live_session.CATEGORY_DECISION,
        live_session.CATEGORY_EXECUTION,
        live_session.CATEGORY_CONTENT,
        live_session.CATEGORY_HITL,
    ]
    serialized = str(rows)
    assert "execution_arguments" not in serialized
    assert "raw_payload" not in serialized


def test_aprobacion_pendiente_no_se_autoaprueba():
    """`approval.pending` sin resolución queda pendiente; con resolución no (T-H01)."""
    session = _session()
    session.events = [
        {"seq": 1, "kind": "approval.pending", "approval_ref": "apr-1", "tool": "write"},
        {"seq": 2, "kind": "approval.pending", "approval_ref": "apr-2", "tool": "write"},
        {"seq": 3, "kind": "approval.resolved", "approval_ref": "apr-2"},
    ]
    pendientes = session.pending_approvals()
    assert [p["approval_ref"] for p in pendientes] == ["apr-1"]


@pytest.mark.parametrize(
    ("state", "espera"),
    [
        ("WAITING_APPROVAL", True),
        ("RESUMING", True),
        ("RUNNING", False),
        ("SUCCEEDED", False),
    ],
)
def test_estado_de_espera(state, espera):
    """Solo los estados de espera se rotulan como HITL pendiente."""
    session = _session()
    session.state = state
    assert session.waiting_approval is espera


@pytest.mark.parametrize(
    ("state", "menciona"),
    [
        ("PARTIAL", "parcial"),
        ("BLOCKED", "gobernanza"),
        ("INDETERMINATE", "timeout"),
        ("FAILED", "sin resultado"),
        ("SUCCEEDED", None),
    ],
)
def test_nota_de_parcialidad(state, menciona):
    """La UI explica la parcialidad sin inventar éxito (T-B09)."""
    session = _session()
    session.state = state
    note = session.partiality_note()
    if menciona is None:
        assert note is None
    else:
        assert note is not None
        assert menciona in note.lower()
