"""Tests del cliente HTTP del dashboard hacia la API de runs (AV-03).

Oráculos: T-B02 (clave de idempotencia enviada), T-B03 (actor por cabecera,
nunca por cuerpo), T-B09 (errores seguros) y T-P05 (sin eco de payload).
"""
from __future__ import annotations

import pytest
import requests

from argentgob.dashboard.live_client import LiveApiClient, LiveApiError


class _Response:
    def __init__(self, status_code: int, payload=None, *, invalid_json: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._invalid = invalid_json

    def json(self):
        if self._invalid:
            raise ValueError("no es JSON")
        return self._payload


class _Session:
    """Sesión falsa que registra la última petición."""

    def __init__(self, response: _Response | Exception):
        self.response = response
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _client(response, token: str | None = "tok") -> tuple[LiveApiClient, _Session]:
    session = _Session(response)
    return LiveApiClient("http://agent:8090", token, session=session), session


def test_create_run_envia_actor_por_cabecera_y_clave():
    """El actor viaja por cabecera y la clave de idempotencia en el cuerpo (T-B03)."""
    client, session = _client(_Response(202, {"run_id": "r1", "state": "STARTING"}))
    client.create_run(
        actor="operador-1",
        scenario_id="finanzas",
        profile="analyst",
        asset="AAPL",
        request_text=None,
        idempotency_key="ui-abc",
    )
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://agent:8090/runs"
    assert call["headers"]["x-argentgob-actor"] == "operador-1"
    assert call["headers"]["x-argentgob-service-token"] == "tok"
    assert call["json"]["idempotency_key"] == "ui-abc"
    assert "actor" not in call["json"]
    assert call["json"]["input"] == {"asset": "AAPL"}


def test_create_run_incluye_request_solo_si_existe():
    """Sin consulta no se envía la clave `request` (esquema estricto)."""
    client, session = _client(_Response(202, {"run_id": "r1", "state": "STARTING"}))
    client.create_run(
        actor="operador-1",
        scenario_id="finanzas",
        profile="analyst",
        asset="AAPL",
        request_text="  ",
        idempotency_key="ui-abc",
    )
    assert "request" not in session.calls[0]["json"]["input"]


def test_get_run_usa_cursor_y_limite():
    """El sondeo envía `after_seq` y `limit` como query (T-B05)."""
    client, session = _client(
        _Response(200, {"run_id": "r1", "state": "RUNNING", "events": []})
    )
    client.get_run("r1", actor="operador-1", after_seq=7, limit=50)
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "http://agent:8090/runs/r1"
    assert call["params"] == {"after_seq": 7, "limit": 50}


@pytest.mark.parametrize(
    ("status", "fragmento"),
    [
        (401, "autenticación"),
        (404, "no existe"),
        (409, "Cupo"),
        (422, "no son válidos"),
        (503, "no está listo"),
    ],
)
def test_errores_http_se_traducen_sin_eco(status, fragmento):
    """Cada código produce un mensaje seguro, sin cuerpo de la API (T-P05)."""
    client, _ = _client(_Response(status, {"detail": "secreto-interno"}))
    with pytest.raises(LiveApiError) as excinfo:
        client.get_run("r1", actor="operador-1")
    assert excinfo.value.status == status
    assert fragmento in excinfo.value.message
    assert "secreto-interno" not in excinfo.value.message


def test_api_caida_es_error_de_transporte():
    """Un fallo de conexión se marca como no disponible (T-B09)."""
    client, _ = _client(requests.ConnectionError("boom"))
    with pytest.raises(LiveApiError) as excinfo:
        client.ready()
    assert excinfo.value.unavailable
    assert excinfo.value.status is None


def test_respuesta_no_json_es_error_seguro():
    """Una respuesta no JSON no propaga la excepción original."""
    client, _ = _client(_Response(200, invalid_json=True))
    with pytest.raises(LiveApiError) as excinfo:
        client.ready()
    assert "inválida" in excinfo.value.message


def test_sin_token_no_envia_cabecera_de_servicio():
    """Sin token configurado no se inventa credencial."""
    client, session = _client(_Response(200, {"ready": True}), token=None)
    client.ready()
    assert "x-argentgob-service-token" not in session.calls[0]["headers"]


def test_base_url_por_defecto_y_sin_barra_final():
    """La base se normaliza sin barra final."""
    client = LiveApiClient("http://agent:8090/", "tok", session=_Session(_Response(200, {})))
    assert client.base_url == "http://agent:8090"
