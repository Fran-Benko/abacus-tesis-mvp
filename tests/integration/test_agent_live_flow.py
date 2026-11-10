"""Integración del flujo de agente en vivo sin LLM (AV-01, T-B04/T-B05/T-B06).

Cubre tres fronteras reales del hito:

1. Canal IPC por socket loopback con handshake de token (rechazo sin token).
2. Proceso hijo (`run_financial_child`) con un Crew sustituido: publica eventos
   de gobernanza reales del PEP y un resultado proyectado.
3. Supervisor completo con el hijo real en un subproceso, usando un módulo de
   Crew falso inyectado por `PYTHONPATH` para no requerir llama.cpp.

El smoke con llama.cpp real se ejecuta aparte (`scripts/verify_agent_live.py`).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from argentgob.live.runner import ChildContext, project_result, run_financial_child
from argentgob.live.supervisor import SocketIpc


class _CollectingChannel:
    """Canal en memoria que registra los mensajes del hijo."""

    def __init__(self):
        self.messages: list[dict] = []

    def put(self, message, timeout=None):
        self.messages.append(message)

    def close(self):
        return None


def test_socket_ipc_rechaza_handshake_sin_token():
    """Un cliente sin el token correcto no entrega mensajes (canal cerrado)."""
    ipc = SocketIpc(accept_timeout=5)
    port = int(ipc.child_env()["ARGENTGOB_IPC_PORT"])

    def fake_child():
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(b'{"type": "hello", "token": "token-invalido"}\n')
        sock.sendall(b'{"type": "final", "state": "SUCCEEDED"}\n')
        sock.close()

    threading.Thread(target=fake_child, daemon=True).start()
    assert list(ipc.messages()) == []
    ipc.close()


def test_socket_ipc_entrega_mensajes_con_token_valido():
    """Con el token correcto, los mensajes JSON llegan en orden."""
    ipc = SocketIpc(accept_timeout=5)
    env = ipc.child_env()
    port = int(env["ARGENTGOB_IPC_PORT"])
    token = env["ARGENTGOB_IPC_TOKEN"]

    def fake_child():
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(
            (json.dumps({"type": "hello", "token": token}) + "\n").encode()
        )
        sock.sendall(b'{"type": "event", "kind": "tool.requested"}\n')
        sock.sendall(b'{"type": "final", "state": "SUCCEEDED"}\n')
        sock.close()

    threading.Thread(target=fake_child, daemon=True).start()
    messages = list(ipc.messages())
    ipc.close()
    assert [m["type"] for m in messages] == ["event", "final"]


def test_hijo_publica_eventos_de_gobernanza_y_resultado(monkeypatch):
    """El hijo emite eventos del PEP real y un resultado proyectado (T-B04)."""
    from argentgob.agent import crew as crew_module
    from argentgob.core.envelope import AgentIdentity
    from argentgob.core.errors import HookAborted
    from argentgob.module_a.governance import GovernanceMiddleware
    from argentgob.module_a.spy_tool import SpyTool
    from argentgob.module_c.guardrails import GuardrailEngine
    from argentgob.core.config import Settings
    from argentgob.core.decision import PolicyDecision
    from argentgob.core.errors import GovernanceAction, Obligation, ReasonCode
    from unittest.mock import MagicMock

    channel = _CollectingChannel()

    class _FakeCrew:
        def kickoff(self):
            settings = Settings(environment="TEST", log_level="WARNING")
            abac = MagicMock()
            abac.evaluate = MagicMock(
                return_value=PolicyDecision(
                    action=GovernanceAction.PASS,
                    reason_code=ReasonCode.ALLOWED,
                    obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
                )
            )
            middleware = GovernanceMiddleware(
                settings=settings,
                abac=abac,
                guardrails=GuardrailEngine(profile_guardrails=[], rate_limit=5),
                audit=MagicMock(),
                run_id="run-integracion-1",
                observer=_ChannelObserver(channel),
            )
            tool = SpyTool(
                governance=middleware,
                agent_identity=AgentIdentity(id="agent-analyst", role="analyst"),
            )
            tool._run(query="AAPL")
            return "Informe de prueba con \x1b[31mANSI\x1b[0m"

    monkeypatch.setattr(
        crew_module, "build_analysis_crew", lambda **kwargs: _FakeCrew()
    )

    ctx = ChildContext(run_id="run-integracion-1", profile="analyst", asset="AAPL", request=None)
    run_financial_child(ctx, channel)

    kinds = [m.get("kind") for m in channel.messages if m["type"] == "event"]
    assert kinds == [
        "tool.requested",
        "governance.decided",
        "tool.started",
        "tool.completed",
    ]
    result = next(m for m in channel.messages if m["type"] == "result")["result"]
    assert "ANSI" in result["summary"]
    assert "\x1b" not in result["summary"]
    final = channel.messages[-1]
    # Una tentativa PASS con resultado v?lido agrega SUCCEEDED (?4).
    assert final == {"type": "final", "state": "SUCCEEDED"}


class _ChannelObserver:
    def __init__(self, channel):
        self._channel = channel

    def on_governance_event(self, kind, payload):
        self._channel.put({"type": "event", "kind": kind, "payload": payload})


def test_hijo_reporta_blocked_sin_fallo_tecnico(monkeypatch):
    """Un HookAborted del PEP se reporta BLOCKED, no FAILED."""
    from argentgob.agent import crew as crew_module
    from argentgob.core.errors import HookAborted

    channel = _CollectingChannel()

    class _BlockedCrew:
        def kickoff(self):
            raise HookAborted(reason="blocked:TOOL_NOT_ALLOWED", source="argentgob.pep")

    monkeypatch.setattr(
        crew_module, "build_analysis_crew", lambda **kwargs: _BlockedCrew()
    )
    ctx = ChildContext(run_id="run-2", profile="analyst", asset="AAPL", request=None)
    run_financial_child(ctx, channel)
    assert channel.messages[-1]["state"] == "BLOCKED"
    assert "TOOL_NOT_ALLOWED" in channel.messages[-1]["message"]


def test_proyeccion_de_resultado_acota_y_marca_truncado():
    """Un informe enorme se recorta y se marca `truncated` (T-P05)."""
    result = project_result("x" * 40000)
    assert result["truncated"] is True
    assert len(json.dumps(result).encode()) <= 32768


def test_supervisor_con_hijo_real_en_subproceso(tmp_path):
    """El supervisor lanza un subproceso real y recibe sus eventos (T-B05)."""
    from argentgob.live.supervisor import RunSupervisor

    # Módulo de Crew falso inyectado por PYTHONPATH: el hijo real lo importa.
    # El paquete falso extiende su `__path__` al paquete real para que
    # `argentgob.live` siga resolviendo desde `src/`.
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
        "class _Crew:\n"
        "    def kickoff(self):\n"
        "        return 'informe del hijo real'\n"
        "\n"
        "def build_analysis_crew(**kwargs):\n"
        "    return _Crew()\n",
        encoding="utf-8",
    )

    monkeypatch_env = {
        "PYTHONPATH": os.pathsep.join([str(tmp_path), src]),
        "ARGENTGOB_REAL_SRC": src,
        "ENVIRONMENT": "TEST",
    }
    previous = {key: os.environ.get(key) for key in monkeypatch_env}
    os.environ.update(monkeypatch_env)
    supervisor = RunSupervisor(run_timeout_seconds=60)
    try:
        record, created = supervisor.submit(
            actor_id="auditor-1",
            scenario_id="finanzas",
            profile="analyst",
            input_payload={"asset": "AAPL"},
            idempotency_key="k-subproceso",
        )
        assert created is True
        finished = supervisor.wait(record.run_id, timeout=60)
        assert finished.state == "SUCCEEDED"
        snapshot = supervisor.snapshot(record.run_id, actor_id="auditor-1")
        assert snapshot["result"]["summary"] == "informe del hijo real"
        assert snapshot["events"][0]["kind"] == "run.accepted"
        assert snapshot["events"][-1]["kind"] == "run.finished"
    finally:
        supervisor.shutdown()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
