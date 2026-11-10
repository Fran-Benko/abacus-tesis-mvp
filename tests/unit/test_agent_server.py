"""Tests de la API interna de runs (AV-01, T-B01/T-B02/T-B03/T-B05/T-B09).

Usan `TestClient` de FastAPI con un supervisor de hijo falso: no hay LLM ni red.
"""
from __future__ import annotations

import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from argentgob.live.api import create_app
from argentgob.live.readiness import ReadinessReport
from argentgob.live.supervisor import RunSupervisor

ACTOR = {"x-argentgob-actor": "auditor-1"}


class _ReadyStack:
    """Readiness de prueba: stack inicializado, sin tocar PostgreSQL (AV-02)."""

    def check(self, *, force: bool = False) -> ReadinessReport:
        return ReadinessReport(
            ready=True,
            checks={"db_reachable": True, "schema_en_head": True, "catalogo_sembrado": True},
            capabilities={
                "llm_base_configured": True,
                "llm_reachable": True,
                "predictor_artifacts_present": True,
            },
            detail={"revision": "008", "policies": 8},
        )

    def invalidate(self) -> None:
        return None


class _FakeHandle:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False

    def join(self, timeout=None):
        return None


class _FakeIpc:
    """Canal IPC en memoria para la API (sin procesos reales)."""

    def __init__(self):
        self._messages = queue.Queue()
        self.closed = False

    def child_env(self):
        return {}

    def put(self, message):
        self._messages.put(message)

    def messages(self):
        while True:
            try:
                message = self._messages.get(timeout=0.05)
            except queue.Empty:
                if self.closed:
                    return
                continue
            if message is None:
                return
            yield message

    def close(self):
        self.closed = True
        self._messages.put(None)


def _spawn_ok(ctx, ipc):
    handle = _FakeHandle()

    def run():
        ipc.put(
            {
                "type": "event",
                "kind": "governance.decided",
                "payload": {"tool": "news", "action": "PASS"},
            }
        )
        ipc.put(
            {
                "type": "result",
                "result": {"summary": "informe", "domain_status": "ANSWERED"},
            }
        )
        ipc.put({"type": "final", "state": "SUCCEEDED"})
        handle.alive = False

    threading.Thread(target=run, daemon=True).start()
    return handle


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "TEST")
    monkeypatch.delenv("AGENT_API_SERVICE_TOKEN", raising=False)
    supervisor = RunSupervisor(spawn=_spawn_ok, ipc_factory=_FakeIpc)
    app = create_app(supervisor, readiness=_ReadyStack())
    with TestClient(app) as test_client:
        yield test_client
    supervisor.shutdown()


def _payload(**overrides):
    body = {
        "scenario_id": "finanzas",
        "profile": "analyst",
        "input": {"asset": "AAPL"},
        "idempotency_key": "intencion-001",
    }
    body.update(overrides)
    return body


def test_health_y_ready_responden(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["ready"] is True
    assert ready["active_run"] is False


def test_post_runs_acepta_y_devuelve_202(client):
    response = client.post("/runs", json=_payload(), headers=ACTOR)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] in ("STARTING", "RUNNING", "SUCCEEDED")
    assert body["query_path"] == f"/runs/{body['run_id']}"
    assert body["poll_after_ms"] > 0


def test_payload_invalido_no_crea_run(client):
    """422 sin eco del payload y sin run creado (T-B01)."""
    response = client.post(
        "/runs", json=_payload(input={"asset": ""}), headers=ACTOR
    )
    assert response.status_code == 422
    assert "AAPL" not in response.text


def test_campo_extra_rechazado(client):
    response = client.post(
        "/runs",
        json=_payload(input={"asset": "AAPL", "url": "http://evil"}),
        headers=ACTOR,
    )
    assert response.status_code == 422


def test_perfil_admin_rechazado(client):
    """`admin` queda fuera de la API de runs (AV-I05, T-B03)."""
    response = client.post("/runs", json=_payload(profile="admin"), headers=ACTOR)
    assert response.status_code == 422


def test_escenario_no_habilitado_rechazado(client):
    response = client.post("/runs", json=_payload(scenario_id="tramites"), headers=ACTOR)
    assert response.status_code == 422


def test_actor_ausente_rechazado(client):
    response = client.post("/runs", json=_payload())
    assert response.status_code == 401


def test_body_demasiado_grande_413(client):
    """Un cuerpo sobre el techo se rechaza antes del parseo (T-B01)."""
    response = client.post(
        "/runs",
        content=b"{" + b"a" * 70000 + b"}",
        headers={**ACTOR, "content-type": "application/json"},
    )
    assert response.status_code == 413


def test_get_run_devuelve_eventos_y_resultado(client):
    created = client.post("/runs", json=_payload(), headers=ACTOR).json()
    run_id = created["run_id"]
    snapshot = None
    for _ in range(200):
        snapshot = client.get(f"/runs/{run_id}", headers=ACTOR).json()
        if snapshot["state"] == "SUCCEEDED":
            break
        time.sleep(0.05)
    assert snapshot is not None
    assert snapshot["state"] == "SUCCEEDED"
    assert snapshot["result"]["summary"] == "informe"
    assert snapshot["predictor_status"] == "NOT_VALIDATED_FOR_DOMAIN"
    kinds = [event["kind"] for event in snapshot["events"]]
    assert kinds[0] == "run.accepted"
    assert kinds[-1] == "run.finished"


def test_get_run_de_otro_actor_404(client):
    created = client.post("/runs", json=_payload(), headers=ACTOR).json()
    response = client.get(
        f"/runs/{created['run_id']}", headers={"x-argentgob-actor": "otro"}
    )
    assert response.status_code == 404


def test_cursor_y_limite_validados(client):
    created = client.post("/runs", json=_payload(), headers=ACTOR).json()
    run_id = created["run_id"]
    assert client.get(f"/runs/{run_id}?after_seq=-1", headers=ACTOR).status_code == 422
    assert client.get(f"/runs/{run_id}?limit=0", headers=ACTOR).status_code == 422
    assert client.get(f"/runs/{run_id}?limit=101", headers=ACTOR).status_code == 422


def test_idempotencia_devuelve_mismo_run(client):
    first = client.post("/runs", json=_payload(), headers=ACTOR).json()
    second = client.post("/runs", json=_payload(), headers=ACTOR).json()
    assert first["run_id"] == second["run_id"]


def test_idempotencia_conflictiva_409(client):
    client.post("/runs", json=_payload(), headers=ACTOR)
    response = client.post(
        "/runs", json=_payload(input={"asset": "MSFT"}), headers=ACTOR
    )
    assert response.status_code == 409


def test_token_de_servicio_requerido_cuando_esta_configurado(monkeypatch):
    """Con token configurado, sin cabecera se rechaza (401)."""
    monkeypatch.setenv("ENVIRONMENT", "TEST")
    monkeypatch.setenv("AGENT_API_SERVICE_TOKEN", "secreto-de-prueba")
    supervisor = RunSupervisor(spawn=_spawn_ok, ipc_factory=_FakeIpc)
    app = create_app(supervisor, readiness=_ReadyStack())
    with TestClient(app) as test_client:
        assert test_client.post("/runs", json=_payload(), headers=ACTOR).status_code == 401
        ok = test_client.post(
            "/runs",
            json=_payload(),
            headers={**ACTOR, "x-argentgob-service-token": "secreto-de-prueba"},
        )
        assert ok.status_code == 202
    supervisor.shutdown()


class _NotReadyStack(_ReadyStack):
    """Readiness de prueba: inicializador fallido (AV-02, T-A04)."""

    def check(self, *, force: bool = False) -> ReadinessReport:
        return ReadinessReport(
            ready=False,
            checks={"db_reachable": True, "schema_en_head": False, "catalogo_sembrado": False},
            capabilities={
                "llm_base_configured": True,
                "llm_reachable": True,
                "predictor_artifacts_present": True,
            },
            detail={"revision": "007", "policies": 0},
        )


def test_stack_no_inicializado_no_admite_runs(monkeypatch):
    """Sin esquema/cat?logo, /ready es False y POST /runs responde 503."""
    monkeypatch.setenv("ENVIRONMENT", "TEST")
    monkeypatch.delenv("AGENT_API_SERVICE_TOKEN", raising=False)
    supervisor = RunSupervisor(spawn=_spawn_ok, ipc_factory=_FakeIpc)
    app = create_app(supervisor, readiness=_NotReadyStack())
    with TestClient(app) as test_client:
        ready = test_client.get("/ready").json()
        assert ready["ready"] is False
        assert ready["checks"]["catalogo_sembrado"] is False
        assert test_client.post("/runs", json=_payload(), headers=ACTOR).status_code == 503
    supervisor.shutdown()


class _NoLlmStack(_ReadyStack):
    """Readiness de prueba: stack listo pero LLM ausente (AV-02, T-A06)."""

    def check(self, *, force: bool = False) -> ReadinessReport:
        return ReadinessReport(
            ready=True,
            checks={"db_reachable": True, "schema_en_head": True, "catalogo_sembrado": True},
            capabilities={
                "llm_base_configured": True,
                "llm_reachable": False,
                "predictor_artifacts_present": True,
            },
            detail={"revision": "008", "policies": 8},
        )


def test_sin_llm_no_admite_runs_pero_sigue_diagnosticando(monkeypatch):
    """Sin LLM: /health y /ready responden; POST /runs responde 503."""
    monkeypatch.setenv("ENVIRONMENT", "TEST")
    monkeypatch.delenv("AGENT_API_SERVICE_TOKEN", raising=False)
    supervisor = RunSupervisor(spawn=_spawn_ok, ipc_factory=_FakeIpc)
    app = create_app(supervisor, readiness=_NoLlmStack())
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200
        ready = test_client.get("/ready").json()
        assert ready["ready"] is True
        assert ready["capabilities"]["llm_reachable"] is False
        assert test_client.post("/runs", json=_payload(), headers=ACTOR).status_code == 503
    supervisor.shutdown()
