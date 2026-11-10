"""Tests AppTest de la vista Finanzas en vivo (AV-03).

Oráculos: T-B02 (doble clic/rerun no repite POST), T-B09 (API caída, estados y
recarga sin excepciones), T-B07 (polling detenido en terminal) y T-P05 (sin
payload crudo ni consola raw en la UI).

La API se sustituye por un cliente falso: estos tests no requieren stack ni LLM.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from argentgob.dashboard import live_client
from argentgob.dashboard.live_client import LiveApiError

_HERE = Path(__file__).resolve().parent
PAGE = str(_HERE / "_live_finance_app.py")


class _FakeClient:
    """Cliente falso con snapshots encolados y contador de admisiones."""

    def __init__(self, *, ready: bool = True, snapshots=None, fail: bool = False):
        self.ready_payload = {
            "ready": ready,
            "checks": {
                "service_token_configured": True,
                "db_reachable": True,
                "schema_en_head": ready,
                "catalogo_sembrado": ready,
            },
            "capabilities": {
                "llm_base_configured": True,
                "llm_reachable": ready,
                "predictor_artifacts_present": True,
                "capacity_free": True,
            },
            "active_run": False,
            "detail": {},
        }
        self.snapshots = list(snapshots or [])
        self.fail = fail
        self.created: list[dict] = []
        self.queries: list[dict] = []

    def ready(self):
        if self.fail:
            raise LiveApiError(None, "API no disponible. Verificá el servicio y reintentá.")
        return self.ready_payload

    def create_run(self, **kwargs):
        if self.fail:
            raise LiveApiError(None, "API no disponible. Verificá el servicio y reintentá.")
        self.created.append(kwargs)
        return {"run_id": "run-abc", "state": "STARTING", "poll_after_ms": 1000}

    def get_run(self, run_id, *, actor, after_seq=0, limit=100):
        if self.fail:
            raise LiveApiError(None, "API no disponible. Verificá el servicio y reintentá.")
        self.queries.append({"run_id": run_id, "after_seq": after_seq})
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


def _app(monkeypatch, client: _FakeClient) -> AppTest:
    monkeypatch.setattr(live_client, "default_client", lambda: client)
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    return at


def test_vista_carga_sin_excepciones(monkeypatch):
    """La vista renderiza con el servicio listo y sin run activo."""
    at = _app(monkeypatch, _FakeClient())
    assert not at.exception
    assert any("Finanzas en vivo" in t.value for t in at.title)


def test_api_caida_no_lanza_excepcion(monkeypatch):
    """Con la API caída la vista informa y no rompe (T-B09)."""
    at = _app(monkeypatch, _FakeClient(fail=True))
    assert not at.exception
    assert any("API no disponible" in w.value for w in at.warning)


def test_servicio_no_listo_se_informa(monkeypatch):
    """`ready=false` se muestra como error con las comprobaciones faltantes."""
    at = _app(monkeypatch, _FakeClient(ready=False))
    assert not at.exception
    assert any("no listo" in e.value for e in at.error)


def test_doble_clic_no_repite_post(monkeypatch):
    """Dos envíos del formulario no crean dos runs (T-B02)."""
    client = _FakeClient()
    at = _app(monkeypatch, client)
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    assert len(client.created) == 1
    assert not at.exception


def test_timeline_muestra_eventos_y_estado(monkeypatch):
    """El timeline lista eventos por categoría y el estado del run (T-B05)."""
    client = _FakeClient(
        snapshots=[
            {
                "run_id": "run-abc",
                "state": "RUNNING",
                "events": [
                    {"seq": 1, "kind": "run.accepted", "state": "STARTING"},
                    {"seq": 2, "kind": "tool.requested", "tool": "market_data"},
                    {
                        "seq": 3,
                        "kind": "governance.decided",
                        "action": "PASS",
                        "reason_codes": ["OK"],
                    },
                ],
                "next_seq": 3,
                "first_available_seq": 1,
                "truncated": False,
                "result": None,
            }
        ]
    )
    at = _app(monkeypatch, client)
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    assert not at.exception
    texto = str(at)
    assert "market_data" in texto
    assert "PASS" in texto
    assert "execution_arguments" not in texto
    assert "raw_payload" not in texto


def test_estado_terminal_detiene_el_sondeo(monkeypatch):
    """En estado terminal no se consulta la API otra vez (T-B07)."""
    client = _FakeClient(
        snapshots=[
            {
                "run_id": "run-abc",
                "state": "SUCCEEDED",
                "events": [{"seq": 1, "kind": "run.finished", "state": "SUCCEEDED"}],
                "next_seq": 1,
                "first_available_seq": 1,
                "truncated": False,
                "result": {"summary": "Informe saneado", "limitations": ["no es asesoramiento"]},
            }
        ]
    )
    at = _app(monkeypatch, client)
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    consultas = len(client.queries)
    at.run()
    assert len(client.queries) == consultas
    assert not at.exception


def test_hitl_pendiente_no_se_aprueba_desde_la_ui(monkeypatch):
    """Un run en WAITING_APPROVAL se informa como pendiente, sin aprobar (T-H01)."""
    client = _FakeClient(
        snapshots=[
            {
                "run_id": "run-abc",
                "state": "WAITING_APPROVAL",
                "events": [
                    {
                        "seq": 1,
                        "kind": "approval.pending",
                        "approval_ref": "apr-1",
                        "tool": "write_report",
                    }
                ],
                "next_seq": 1,
                "first_available_seq": 1,
                "truncated": False,
                "result": None,
            }
        ]
    )
    at = _app(monkeypatch, client)
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    assert not at.exception
    assert "apr-1" in str(at)
    assert any("no aprueba" in i.value for i in at.info)


def test_activo_vacio_no_admite_run(monkeypatch):
    """Un activo vacío se rechaza en la UI sin llamar a la API (T-B01)."""
    client = _FakeClient()
    at = _app(monkeypatch, client)
    at.text_input(key="lf_asset").set_value("   ")
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    assert not client.created
    assert any("activo" in e.value.lower() for e in at.error)


@pytest.mark.parametrize("perfil", ["analyst", "analyst_restricted"])
def test_perfiles_operables(monkeypatch, perfil):
    """Solo los perfiles del catálogo cerrado son seleccionables (T-B03)."""
    client = _FakeClient()
    at = _app(monkeypatch, client)
    at.selectbox(key="lf_profile").set_value(perfil)
    at.button(key="FormSubmitter:live_finance_form-Iniciar análisis en vivo").click().run()
    assert client.created[0]["profile"] == perfil
    assert "admin" not in str(at.selectbox(key="lf_profile").options)
