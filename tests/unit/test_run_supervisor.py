"""Tests del supervisor de runs (AV-01, T-B02/T-B05/T-B07/T-B08).

Usan un hijo falso (sin LLM ni red) y un canal IPC en memoria para verificar
admisión, idempotencia, cupo, eventos consultables antes del final, timeout y
liberación de cupo.
"""
from __future__ import annotations

import queue
import threading
import time

import pytest

from argentgob.live.supervisor import (
    RunCapacityError,
    RunConflictError,
    RunNotFoundError,
    RunSupervisor,
)


class _FakeHandle:
    """Handle de proceso falso."""

    def __init__(self):
        self.alive = True
        self.terminated = False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.terminated = True
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        return None


class _FakeIpc:
    """Canal IPC en memoria: el hijo falso publica mensajes en una cola."""

    def __init__(self):
        self._messages: queue.Queue = queue.Queue()
        self.closed = False

    def child_env(self) -> dict[str, str]:
        return {}

    def put(self, message: dict) -> None:
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

    def close(self) -> None:
        self.closed = True
        self._messages.put(None)


def _spawn_with(script):
    """Crea un spawn que ejecuta `script(ipc)` en un hilo y devuelve el handle."""
    def spawn(ctx, ipc):
        handle = _FakeHandle()

        def run():
            try:
                script(ipc)
            except Exception:  # noqa: BLE001 - el hijo falso muere sin mensaje final
                pass
            finally:
                handle.alive = False
                # Un proceso real cierra el socket al morir: el canal termina.
                ipc.close()

        threading.Thread(target=run, daemon=True).start()
        return handle

    return spawn


def _ok_script(ipc):
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


def _blocked_script(ipc):
    ipc.put({"type": "final", "state": "BLOCKED", "message": "blocked:TOOL_NOT_ALLOWED"})


def _slow_script(ipc):
    time.sleep(30)


def _crash_script(ipc):
    raise RuntimeError("fallo simulado del hijo")


def _supervisor(script, **kwargs) -> RunSupervisor:
    return RunSupervisor(spawn=_spawn_with(script), ipc_factory=_FakeIpc, **kwargs)


def _submit(supervisor: RunSupervisor, *, key: str = "k-1", asset: str = "AAPL"):
    return supervisor.submit(
        actor_id="auditor-1",
        scenario_id="finanzas",
        profile="analyst",
        input_payload={"asset": asset},
        idempotency_key=key,
    )


def test_run_exitoso_publica_eventos_y_resultado():
    """Un run PASS termina SUCCEEDED con eventos y resultado proyectado."""
    supervisor = _supervisor(_ok_script)
    record, created = _submit(supervisor)
    assert created is True
    finished = supervisor.wait(record.run_id, timeout=20)
    assert finished.state == "SUCCEEDED"
    snapshot = supervisor.snapshot(record.run_id, actor_id="auditor-1")
    kinds = [event["kind"] for event in snapshot["events"]]
    assert kinds[0] == "run.accepted"
    assert "governance.decided" in kinds
    assert kinds[-1] == "run.finished"
    assert snapshot["result"]["summary"] == "informe"
    supervisor.shutdown()


def test_idempotencia_misma_clave_mismo_input_no_crea_otro_run():
    """Repetición válida devuelve el mismo run sin segundo proceso (T-B02)."""
    supervisor = _supervisor(_ok_script)
    first, created1 = _submit(supervisor, key="k-1")
    supervisor.wait(first.run_id, timeout=20)
    second, created2 = _submit(supervisor, key="k-1")
    assert created1 is True
    assert created2 is False
    assert second.run_id == first.run_id
    supervisor.shutdown()


def test_idempotencia_con_otro_input_produce_conflicto():
    """Misma clave con input distinto → conflicto (409)."""
    supervisor = _supervisor(_ok_script)
    first, _ = _submit(supervisor, key="k-1", asset="AAPL")
    supervisor.wait(first.run_id, timeout=20)
    with pytest.raises(RunConflictError):
        _submit(supervisor, key="k-1", asset="MSFT")
    supervisor.shutdown()


def test_cupo_unico_rechaza_segundo_run_activo():
    """Con un run activo, otro submit se rechaza (T-B02)."""
    supervisor = _supervisor(_slow_script, run_timeout_seconds=30)
    _submit(supervisor, key="k-1")
    with pytest.raises(RunCapacityError):
        _submit(supervisor, key="k-2")
    supervisor.shutdown()
    assert supervisor.active_run_id is None


def test_evento_consultable_antes_del_final():
    """El evento de arranque es visible mientras el run sigue activo (T-B06)."""
    supervisor = _supervisor(_slow_script, run_timeout_seconds=30)
    record, _ = _submit(supervisor)
    deadline = time.monotonic() + 10
    seen = False
    while time.monotonic() < deadline and not seen:
        snapshot = supervisor.snapshot(record.run_id, actor_id="auditor-1")
        seen = any(e["kind"] == "run.started" for e in snapshot["events"])
        assert snapshot["state"] == "RUNNING"
        time.sleep(0.02)
    assert seen is True
    supervisor.shutdown()


def test_timeout_termina_y_libera_cupo():
    """Un hijo colgado termina TIMED_OUT y libera el cupo (T-B08)."""
    supervisor = _supervisor(_slow_script, run_timeout_seconds=1)
    record, _ = _submit(supervisor)
    finished = supervisor.wait(record.run_id, timeout=20)
    assert finished.state == "TIMED_OUT"
    assert supervisor.active_run_id is None
    supervisor.shutdown()


def test_hijo_que_falla_termina_failed():
    """Una excepción del hijo no deja el run colgado."""
    supervisor = _supervisor(_crash_script)
    record, _ = _submit(supervisor)
    finished = supervisor.wait(record.run_id, timeout=20)
    assert finished.state == "FAILED"
    assert supervisor.active_run_id is None
    supervisor.shutdown()


def test_block_de_gobernanza_termina_blocked():
    """Un BLOCK del PEP se reporta como BLOCKED, no como fallo técnico."""
    supervisor = _supervisor(_blocked_script)
    record, _ = _submit(supervisor)
    finished = supervisor.wait(record.run_id, timeout=20)
    assert finished.state == "BLOCKED"
    supervisor.shutdown()


def test_run_de_otro_actor_no_es_visible():
    """Un actor ajeno recibe 404 uniforme (T-B03)."""
    supervisor = _supervisor(_ok_script)
    record, _ = _submit(supervisor)
    supervisor.wait(record.run_id, timeout=20)
    with pytest.raises(RunNotFoundError):
        supervisor.snapshot(record.run_id, actor_id="otro-actor")
    supervisor.shutdown()


def test_purga_de_runs_terminales_acota_memoria():
    """Los runs terminales se purgan por TTL y por cantidad (T-B07)."""
    supervisor = _supervisor(_ok_script, max_terminal_runs=2, terminal_ttl_seconds=0)
    for index in range(4):
        record, _ = _submit(supervisor, key=f"k-{index}")
        supervisor.wait(record.run_id, timeout=20)
    assert len(supervisor._runs) <= 2
    supervisor.shutdown()


def test_spawn_fallido_termina_failed_y_libera_cupo():
    """Si el proceso no arranca, el run falla seguro y el cupo queda libre."""
    def _broken_spawn(ctx, ipc):
        raise OSError("no se pudo lanzar el hijo")

    supervisor = RunSupervisor(spawn=_broken_spawn, ipc_factory=_FakeIpc)
    record, _ = _submit(supervisor)
    finished = supervisor.wait(record.run_id, timeout=10)
    assert finished.state == "FAILED"
    assert supervisor.active_run_id is None
    supervisor.shutdown()
