"""Supervisor de runs: un proceso hijo por run, cupo único y buffer acotado.

Contrato ([contratos.md] §3/§4):

- Un run activo global; el cupo se libera **solo** después de confirmar la
  muerte del proceso hijo.
- Idempotencia atómica por (actor, clave): misma clave + mismo input devuelve
  el run existente; misma clave con otro input produce conflicto.
- Eventos consultables antes de que el run termine (buffer en el padre).
- TTL de runs terminales y tombstones acotados.
- Timeout con efecto desconocido termina `INDETERMINATE`, nunca `FAILED`.

El canal IPC es explícito: un socket loopback autenticado con un token de un
solo uso. stdout/stderr del hijo se descartan y nunca se convierten en eventos.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Iterator, Protocol

from argentgob.live.events import TERMINAL_STATES, EventBuffer, RunEvent
from argentgob.live.runner import ChildContext
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

DEFAULT_MAX_EVENTS = 256
DEFAULT_MAX_EVENT_BYTES = 2048
DEFAULT_RUN_TIMEOUT_SECONDS = 180
DEFAULT_TERMINAL_TTL_SECONDS = 900
DEFAULT_MAX_TERMINAL_RUNS = 10
DEFAULT_POLL_AFTER_MS = 1000
_ACCEPT_TIMEOUT_SECONDS = 15.0


class RunConflictError(Exception):
    """La clave de idempotencia ya existe con otro input (HTTP 409)."""


class RunCapacityError(Exception):
    """Ya hay un run activo (HTTP 409)."""


class RunNotFoundError(Exception):
    """El run no existe o no es visible para el actor (HTTP 404)."""


class ProcessHandle(Protocol):
    """Handle mínimo del proceso hijo."""

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class IpcChannel(Protocol):
    """Canal IPC del run: entrega mensajes del hijo al padre."""

    def child_env(self) -> dict[str, str]:
        """Variables de entorno que el hijo necesita para conectarse."""
        ...

    def messages(self) -> Iterator[dict[str, Any]]:
        """Itera mensajes hasta el cierre del canal."""
        ...

    def close(self) -> None: ...


class SocketIpc:
    """Canal IPC por socket loopback con handshake de token."""

    def __init__(self, *, accept_timeout: float = _ACCEPT_TIMEOUT_SECONDS):
        self._token = secrets.token_urlsafe(24)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(accept_timeout)
        self._port = self._listener.getsockname()[1]
        self._closed = False

    def child_env(self) -> dict[str, str]:
        return {
            "ARGENTGOB_IPC_PORT": str(self._port),
            "ARGENTGOB_IPC_TOKEN": self._token,
        }

    def messages(self) -> Iterator[dict[str, Any]]:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        try:
            conn.settimeout(None)
            stream = conn.makefile("r", encoding="utf-8", newline="\n")
            handshake = stream.readline()
            if not _handshake_ok(handshake, self._token):
                return
            for line in stream:
                message = _parse_message(line)
                if message is not None:
                    yield message
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass


def _handshake_ok(line: str, token: str) -> bool:
    try:
        payload = json.loads(line)
    except (ValueError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("type") == "hello"
        and secrets.compare_digest(str(payload.get("token", "")), token)
    )


def _parse_message(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


class _SubprocessHandle:
    """Handle de un proceso hijo real lanzado con `subprocess`."""

    def __init__(self, process: subprocess.Popen):
        self._process = process

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def join(self, timeout: float | None = None) -> None:
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def spawn_subprocess(ctx: ChildContext, ipc: IpcChannel) -> ProcessHandle:
    """Lanza el hijo real como módulo Python con entorno acotado."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONUNBUFFERED": "1",
        "ARGENTGOB_RUN_ID": ctx.run_id,
        "ARGENTGOB_PROFILE": ctx.profile,
        "ARGENTGOB_ASSET": ctx.asset,
        "ARGENTGOB_MAX_EVENT_BYTES": str(ctx.max_event_bytes),
        # El hijo necesita la misma configuración de gobernanza y LLM.
        **{
            key: value
            for key, value in os.environ.items()
            if key.startswith(("OPENAI_", "DATABASE_", "ENVIRONMENT", "LOG_LEVEL"))
            or key.startswith(("MAX_", "RATE_LIMIT_", "HOLD_", "APPROVAL_"))
            or key.startswith(("IDENTITY_", "AUTHORIZED_", "RISK_MODEL"))
            or key.startswith("ARGENTGOB_")
        },
        **ipc.child_env(),
    }
    if ctx.request:
        env["ARGENTGOB_REQUEST"] = ctx.request
    process = subprocess.Popen(  # noqa: S603 - comando fijo, sin shell
        [sys.executable, "-m", "argentgob.live.child"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return _SubprocessHandle(process)


@dataclass
class RunRecord:
    """Estado en memoria de un run supervisado."""

    run_id: str
    actor_id: str
    scenario_id: str
    profile: str
    idempotency_key: str
    input_digest: str
    state: str = "STARTING"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    buffer: EventBuffer = field(default_factory=EventBuffer)
    handle: ProcessHandle | None = None
    ipc: IpcChannel | None = None
    reader: threading.Thread | None = None
    terminal_reason: str | None = None
    final_received: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def _digest_input(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class RunSupervisor:
    """Orquesta runs: admisión, proceso hijo, eventos y liberación de cupo."""

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
        terminal_ttl_seconds: int = DEFAULT_TERMINAL_TTL_SECONDS,
        max_terminal_runs: int = DEFAULT_MAX_TERMINAL_RUNS,
        spawn: Callable[[ChildContext, IpcChannel], ProcessHandle] | None = None,
        ipc_factory: Callable[[], IpcChannel] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.max_events = max_events
        self.max_event_bytes = max_event_bytes
        self.run_timeout_seconds = run_timeout_seconds
        self.terminal_ttl_seconds = terminal_ttl_seconds
        self.max_terminal_runs = max_terminal_runs
        self._spawn = spawn or spawn_subprocess
        self._ipc_factory = ipc_factory or SocketIpc
        self._clock = clock or (lambda: datetime.now(UTC))
        self._runs: dict[str, RunRecord] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()
        self._active_run_id: str | None = None

    # ── Admisión ─────────────────────────────────────────────────────
    def submit(
        self,
        *,
        actor_id: str,
        scenario_id: str,
        profile: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[RunRecord, bool]:
        """Admite un run. Devuelve (registro, creado).

        `creado=False` significa repetición idempotente válida: no se lanza un
        segundo proceso.
        """
        digest = _digest_input(input_payload)
        key = (actor_id, idempotency_key)
        with self._lock:
            self._purge_expired()
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                existing = self._runs.get(existing_id)
                if existing is not None:
                    if existing.input_digest != digest:
                        raise RunConflictError(
                            "idempotency_key reutilizada con otro input"
                        )
                    return existing, False
            if self._active_run_id is not None:
                active = self._runs.get(self._active_run_id)
                if active is not None and not active.is_terminal:
                    raise RunCapacityError("ya hay un run activo")

            run_id = str(uuid.uuid4())
            record = RunRecord(
                run_id=run_id,
                actor_id=actor_id,
                scenario_id=scenario_id,
                profile=profile,
                idempotency_key=idempotency_key,
                input_digest=digest,
                buffer=EventBuffer(
                    max_events=self.max_events,
                    max_event_bytes=self.max_event_bytes,
                ),
            )
            self._runs[run_id] = record
            self._idempotency[key] = run_id
            self._active_run_id = run_id
            record.buffer.append(run_id, "run.accepted", {"state": "STARTING"})

        self._start_child(record, input_payload)
        return record, True

    def _start_child(self, record: RunRecord, input_payload: dict[str, Any]) -> None:
        """Crea el canal IPC, lanza el hijo y arranca el lector de eventos."""
        ipc = self._ipc_factory()
        child_ctx = ChildContext(
            run_id=record.run_id,
            profile=record.profile,
            asset=str(input_payload.get("asset", "")),
            request=input_payload.get("request"),
            max_event_bytes=self.max_event_bytes,
        )
        with self._lock:
            record.ipc = ipc
            record.state = "RUNNING"
            record.buffer.append(record.run_id, "run.started", {"state": "RUNNING"})
        try:
            handle = self._spawn(child_ctx, ipc)
        except Exception as exc:  # noqa: BLE001 - fallo de arranque seguro
            log.error(
                "child_spawn_failed",
                run_id=record.run_id,
                error_type=type(exc).__name__,
            )
            self._finish(record, "FAILED", reason="child_spawn_failed")
            return
        with self._lock:
            record.handle = handle
        reader = threading.Thread(
            target=self._drain_channel,
            args=(record,),
            name=f"argentgob-events-{record.run_id[:8]}",
            daemon=True,
        )
        record.reader = reader
        reader.start()
        watchdog = threading.Thread(
            target=self._watch_timeout,
            args=(record,),
            name=f"argentgob-timeout-{record.run_id[:8]}",
            daemon=True,
        )
        watchdog.start()

    # ── Lectura de eventos ───────────────────────────────────────────
    def _drain_channel(self, record: RunRecord) -> None:
        """Consume mensajes del hijo hasta el cierre del canal."""
        ipc = record.ipc
        if ipc is None:
            self._finish(record, "FAILED", reason="ipc_missing")
            return
        try:
            for message in ipc.messages():
                kind = message.get("type")
                if kind == "event":
                    record.buffer.append(
                        record.run_id,
                        str(message.get("kind", "")),
                        message.get("payload") or {},
                    )
                elif kind == "result":
                    with self._lock:
                        record.result = message.get("result")
                elif kind == "final":
                    with self._lock:
                        record.final_received = True
                    self._finish(
                        record,
                        str(message.get("state", "FAILED")),
                        reason=message.get("message"),
                    )
                    return
        except Exception as exc:  # noqa: BLE001 - canal roto: cierre seguro
            log.warning(
                "ipc_read_failed",
                run_id=record.run_id,
                error_type=type(exc).__name__,
            )
        with self._lock:
            already_final = record.final_received or record.is_terminal
        if not already_final:
            self._finish(record, "FAILED", reason="child_exit_without_final")

    def _watch_timeout(self, record: RunRecord) -> None:
        """Aplica el timeout del run y termina el hijo si excede el presupuesto."""
        deadline = time.monotonic() + self.run_timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if record.is_terminal:
                    return
            time.sleep(0.05)
        with self._lock:
            if record.is_terminal:
                return
        self._finish(record, "TIMED_OUT", reason="run_timeout")

    def _finish(self, record: RunRecord, state: str, *, reason: str | None = None) -> None:
        """Cierra el run, confirma la muerte del hijo y libera el cupo.

        El cupo se libera **después** de confirmar la terminación del proceso
        hijo y antes de publicar el estado terminal, de modo que un observador
        que vea el run terminado también vea el cupo libre (T-B08).
        """
        if state not in TERMINAL_STATES:
            state = "FAILED"
        with self._lock:
            if record.is_terminal:
                return
        self._terminate_child(record)
        with self._lock:
            if record.is_terminal:
                return
            record.state = state
            record.terminal_reason = reason
            record.finished_at = self._clock()
            record.buffer.append(
                record.run_id,
                "run.finished",
                {"state": state, "message": reason},
            )
            if self._active_run_id == record.run_id:
                self._active_run_id = None

    def _terminate_child(self, record: RunRecord) -> None:
        """Termina cooperativamente y confirma la muerte del proceso hijo."""
        handle = record.handle
        if handle is not None:
            try:
                handle.join(timeout=5)
                if handle.is_alive():
                    handle.terminate()
                    handle.join(timeout=5)
                if handle.is_alive():
                    handle.kill()
                    handle.join(timeout=5)
            except Exception as exc:  # noqa: BLE001 - no bloquear la liberación
                log.warning(
                    "child_termination_failed",
                    run_id=record.run_id,
                    error_type=type(exc).__name__,
                )
        if record.ipc is not None:
            try:
                record.ipc.close()
            except Exception:  # noqa: BLE001 - canal ya cerrado
                pass

    # ── Consulta ─────────────────────────────────────────────────────
    def get(self, run_id: str, *, actor_id: str) -> RunRecord:
        """Devuelve el run si pertenece al actor; 404 uniforme en otro caso."""
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.actor_id != actor_id:
                raise RunNotFoundError("run no encontrado")
            return record

    def snapshot(
        self, run_id: str, *, actor_id: str, after_seq: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Snapshot completo de estado + página de eventos por cursor."""
        record = self.get(run_id, actor_id=actor_id)
        events, next_seq, first_available, truncated = record.buffer.page(
            after_seq=after_seq, limit=limit
        )
        return {
            "schema_version": "1",
            "run_id": record.run_id,
            "state": record.state,
            "events": [event.to_dict() for event in events],
            "next_seq": next_seq,
            "first_available_seq": first_available,
            "truncated": truncated,
            "result": record.result if record.is_terminal else None,
            "predictor_status": "NOT_VALIDATED_FOR_DOMAIN",
        }

    def wait(self, run_id: str, *, timeout: float = 30.0) -> RunRecord:
        """Espera (para tests/gate) a que el run alcance un estado terminal."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                record = self._runs.get(run_id)
                if record is not None and record.is_terminal:
                    return record
            time.sleep(0.02)
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise RunNotFoundError("run no encontrado")
        return record

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    # ── Purga ────────────────────────────────────────────────────────
    def _purge_expired(self) -> None:
        """Elimina runs terminales vencidos y tombstones de idempotencia."""
        now = self._clock()
        ttl = timedelta(seconds=self.terminal_ttl_seconds)
        expired = [
            run_id
            for run_id, record in self._runs.items()
            if record.is_terminal
            and record.finished_at is not None
            and now - record.finished_at > ttl
        ]
        for run_id in expired:
            record = self._runs.pop(run_id, None)
            if record is not None:
                self._idempotency.pop(
                    (record.actor_id, record.idempotency_key), None
                )
        terminal = [
            record
            for record in self._runs.values()
            if record.is_terminal and record.finished_at is not None
        ]
        if len(terminal) > self.max_terminal_runs:
            terminal.sort(key=lambda r: r.finished_at)
            for record in terminal[: len(terminal) - self.max_terminal_runs]:
                self._runs.pop(record.run_id, None)
                self._idempotency.pop(
                    (record.actor_id, record.idempotency_key), None
                )

    def shutdown(self) -> None:
        """Termina todos los hijos vivos (cierre ordenado del servicio)."""
        with self._lock:
            records = list(self._runs.values())
        for record in records:
            if not record.is_terminal:
                self._finish(record, "FAILED", reason="service_shutdown")
            else:
                self._terminate_child(record)


def events_to_dicts(events: list[RunEvent]) -> list[dict[str, Any]]:
    """Utilidad de proyección para tests y evidencia."""
    return [event.to_dict() for event in events]
