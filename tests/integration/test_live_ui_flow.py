"""Integración UI → API → supervisor → hijo real (AV-03).

Levanta el servidor ASGI real en loopback y usa el cliente del dashboard
(`LiveApiClient`) con la sesión de UI (`LiveSession`) para verificar:

- T-B06: un evento es visible **antes** de que el run termine y la visibilidad
  medida es ≤ 3 s.
- T-B05: el cursor avanza sin duplicar eventos entre sondeos.
- T-B02: repetir la admisión con la misma clave no crea otro proceso.
- T-B07: el sondeo cesa en estado terminal.
- T-B09: la UI refleja el estado terminal y el resultado proyectado.

El Crew se sustituye por un módulo falso inyectado por `PYTHONPATH` (el hijo
real lo importa); no se requiere llama.cpp.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from argentgob.dashboard import live_session
from argentgob.dashboard.live_client import LiveApiClient
from argentgob.live.api import create_app
from argentgob.live.supervisor import RunSupervisor

VISIBILITY_BUDGET_SECONDS = 3.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def fake_crew(tmp_path, monkeypatch):
    """Módulo `argentgob.agent.crew` falso que emite eventos y tarda."""
    src = str(Path(__file__).resolve().parents[2] / "src")
    fake_pkg = tmp_path / "argentgob" / "agent"
    fake_pkg.mkdir(parents=True)
    (tmp_path / "argentgob" / "__init__.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "_real = Path(os.environ['ARGENTGOB_REAL_SRC']) / 'argentgob'\n"
        "__path__ = [str(Path(__file__).parent), str(_real)]\n",
        encoding="utf-8",
    )
    (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
    (fake_pkg / "crew.py").write_text(
        "import time\n"
        "\n"
        "class _Crew:\n"
        "    def __init__(self, observer=None):\n"
        "        self._observer = observer\n"
        "\n"
        "    def kickoff(self):\n"
        "        if self._observer is not None:\n"
        "            self._observer.on_governance_event(\n"
        "                'tool.requested', {'tool': 'market_data', 'attempt': 1}\n"
        "            )\n"
        "            self._observer.on_governance_event(\n"
        "                'governance.decided',\n"
        "                {'tool': 'market_data', 'action': 'PASS', 'reason_codes': ['OK']},\n"
        "            )\n"
        "        time.sleep(2.0)\n"
        "        return 'informe incremental de prueba'\n"
        "\n"
        "def build_analysis_crew(**kwargs):\n"
        "    return _Crew(observer=kwargs.get('observer'))\n",
        encoding="utf-8",
    )
    env = {
        "PYTHONPATH": os.pathsep.join([str(tmp_path), src]),
        "ARGENTGOB_REAL_SRC": src,
        "ENVIRONMENT": "TEST",
    }
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _ReadyStack:
    """Readiness fija: el flujo de UI no depende de PostgreSQL en este test."""

    def check(self, *, force: bool = False):
        from argentgob.live.readiness import ReadinessReport

        return ReadinessReport(
            ready=True,
            checks={
                "db_reachable": True,
                "schema_en_head": True,
                "catalogo_sembrado": True,
            },
            capabilities={
                "llm_base_configured": True,
                "llm_reachable": True,
                "predictor_artifacts_present": True,
            },
            detail={"revision": "010", "policies": 8},
        )


@pytest.fixture()
def api_server(fake_crew):
    """Servidor ASGI real en loopback con supervisor propio."""
    supervisor = RunSupervisor(run_timeout_seconds=60)
    app = create_app(supervisor=supervisor, readiness=_ReadyStack())
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("el servidor ASGI no arrancó")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        supervisor.shutdown()


def _wait_for_event(session, client, *, timeout: float) -> float:
    """Sondea hasta ver el primer evento; devuelve los segundos transcurridos."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        live_session.poll(session, client)
        if session.events:
            return time.monotonic() - started
        time.sleep(0.1)
    return time.monotonic() - started


def test_evento_visible_antes_de_terminar_y_en_menos_de_3s(api_server):
    """Un evento llega con el run aún activo y dentro del presupuesto (T-B06)."""
    client = LiveApiClient(api_server, None, timeout=5)
    session = live_session.LiveSession(actor="operador-1", profile="analyst", asset="AAPL")
    live_session.start(session, client)
    assert session.run_id, session.error

    elapsed = _wait_for_event(session, client, timeout=VISIBILITY_BUDGET_SECONDS)
    assert session.events, "no se observó ningún evento incremental"
    assert elapsed <= VISIBILITY_BUDGET_SECONDS
    assert not session.terminal, "el evento llegó recién al terminar el run"
    assert session.events[0]["kind"] in ("run.accepted", "run.started", "tool.requested")


def test_cursor_avanza_sin_duplicar_y_termina(api_server):
    """El cursor avanza, no duplica eventos y el polling cesa (T-B05/T-B07)."""
    client = LiveApiClient(api_server, None, timeout=5)
    session = live_session.LiveSession(actor="operador-1", profile="analyst", asset="MSFT")
    live_session.start(session, client)
    assert session.run_id, session.error

    deadline = time.monotonic() + 30
    while not session.terminal and time.monotonic() < deadline:
        live_session.poll(session, client)
        time.sleep(0.2)

    assert session.terminal, f"el run no terminó: {session.state}"
    seqs = [int(e["seq"]) for e in session.events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert session.cursor == max(seqs)
    assert session.result is not None
    assert "informe incremental" in session.result["summary"]

    polls_before = session.polls
    live_session.poll(session, client)
    assert session.polls == polls_before


def test_misma_intencion_no_crea_otro_run(api_server):
    """Repetir la admisión con la misma clave devuelve el mismo run (T-B02)."""
    client = LiveApiClient(api_server, None, timeout=5)
    first = live_session.LiveSession(actor="operador-1", profile="analyst", asset="TSLA")
    live_session.start(first, client)
    assert first.run_id, first.error

    second = live_session.LiveSession(actor="operador-1", profile="analyst", asset="TSLA")
    assert second.idempotency_key == first.idempotency_key
    live_session.start(second, client)
    assert second.run_id == first.run_id


def test_actor_ajeno_no_ve_el_run(api_server):
    """Otro actor recibe 404 uniforme y la UI lo informa sin traza (T-B03)."""
    client = LiveApiClient(api_server, None, timeout=5)
    session = live_session.LiveSession(actor="operador-1", profile="analyst", asset="NVDA")
    live_session.start(session, client)
    assert session.run_id, session.error

    intruso = live_session.LiveSession(actor="operador-2", profile="analyst", asset="NVDA")
    intruso.run_id = session.run_id
    live_session.poll(intruso, client)
    assert intruso.error is not None
    assert "no existe" in intruso.error
    assert "Traceback" not in intruso.error
