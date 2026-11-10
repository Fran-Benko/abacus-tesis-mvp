"""Eventos de run: allowlist, buffer acotado y observador de cola (AV-01).

Reglas del contrato ([contratos.md] §4):

- Campos obligatorios: `schema_version`, `run_id`, `seq` creciente, timestamp
  UTC y `kind`.
- Solo se publican campos de una allowlist; nunca argumentos operativos,
  prompts, razonamiento ni stdout.
- El buffer es un anillo acotado (eventos y bytes por evento) con TTL de run.
  Si el anillo descarta eventos, la consulta lo informa con `truncated` y
  `first_available_seq`; el snapshot de estado sigue completo.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "1"

# Tipos de evento del contrato. Un `kind` fuera de esta lista se descarta.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        "run.accepted",
        "run.started",
        "tool.requested",
        "governance.decided",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "content.released",
        "content.withheld",
        "approval.pending",
        "approval.resolved",
        "execution.resumed",
        "run.finished",
    }
)

# Estados del run (contrato §4).
RUN_STATES: frozenset[str] = frozenset(
    {
        "STARTING",
        "RUNNING",
        "WAITING_APPROVAL",
        "RESUMING",
        "SUCCEEDED",
        "PARTIAL",
        "BLOCKED",
        "FAILED",
        "TIMED_OUT",
        "REJECTED",
        "EXPIRED",
        "INDETERMINATE",
    }
)

TERMINAL_STATES: frozenset[str] = frozenset(
    {
        "SUCCEEDED",
        "PARTIAL",
        "BLOCKED",
        "FAILED",
        "TIMED_OUT",
        "REJECTED",
        "EXPIRED",
        "INDETERMINATE",
    }
)

# Allowlist de campos publicables. Todo lo demás se descarta en la frontera.
_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "event_id",
        "decision_id",
        "tool",
        "resource",
        "operation_class",
        "action",
        "reason_codes",
        "risk_level",
        "policy_id",
        "execution_status",
        "content_status",
        "latency_ms",
        "approval_ref",
        "state",
        "message",
        "result_summary",
        "predictor_status",
        "attempt",
    }
)

_MAX_LIST_ITEMS = 8
_MAX_CODE_CHARS = 64
_MAX_MESSAGE_CHARS = 200

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_unsafe_text(value: str, *, max_chars: int) -> str:
    """Quita ANSI/controles y acota longitud (salida segura, T-P05)."""
    cleaned = _CONTROL_RE.sub("", _ANSI_RE.sub("", value))
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + "…"
    return cleaned


def sanitize_payload(payload: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Proyecta un payload por allowlist y acota su tamaño serializado."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in _ALLOWED_FIELDS or value is None:
            continue
        if key == "reason_codes":
            if isinstance(value, (list, tuple)):
                clean[key] = [
                    strip_unsafe_text(str(item), max_chars=_MAX_CODE_CHARS)
                    for item in list(value)[:_MAX_LIST_ITEMS]
                ]
            continue
        if key == "latency_ms":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[key] = round(float(value), 2)
            continue
        if key == "attempt":
            if isinstance(value, int) and not isinstance(value, bool):
                clean[key] = value
            continue
        if key == "result_summary":
            clean[key] = strip_unsafe_text(str(value), max_chars=max_bytes)
            continue
        if key == "message":
            clean[key] = strip_unsafe_text(str(value), max_chars=_MAX_MESSAGE_CHARS)
            continue
        clean[key] = strip_unsafe_text(str(value), max_chars=_MAX_CODE_CHARS)

    if _serialized_size(clean) <= max_bytes:
        return clean

    # Segundo intento: descartar el resumen de resultado (el más voluminoso).
    clean.pop("result_summary", None)
    if _serialized_size(clean) <= max_bytes:
        return clean

    # Último recurso: conservar solo la metadata mínima.
    minimal = {
        key: clean[key]
        for key in ("event_id", "decision_id", "tool", "action", "state")
        if key in clean
    }
    minimal["truncated"] = True
    return minimal


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


@dataclass(frozen=True)
class RunEvent:
    """Evento inmutable de un run, con `seq` monotónica por run."""

    run_id: str
    seq: int
    kind: str
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat(),
            "kind": self.kind,
            **self.payload,
        }


class EventBuffer:
    """Anillo acotado de eventos por run, seguro entre hilos."""

    def __init__(self, *, max_events: int = 256, max_event_bytes: int = 2048):
        if max_events <= 0:
            raise ValueError("max_events debe ser positivo")
        if max_event_bytes <= 0:
            raise ValueError("max_event_bytes debe ser positivo")
        self.max_events = max_events
        self.max_event_bytes = max_event_bytes
        self._events: list[RunEvent] = []
        self._lock = threading.Lock()
        self._last_seq = 0
        self._dropped = 0

    def append(
        self, run_id: str, kind: str, payload: dict[str, Any] | None = None
    ) -> RunEvent | None:
        """Agrega un evento; devuelve None si el `kind` no está en la allowlist."""
        if kind not in EVENT_KINDS:
            return None
        clean = sanitize_payload(payload or {}, max_bytes=self.max_event_bytes)
        with self._lock:
            self._last_seq += 1
            event = RunEvent(
                run_id=run_id,
                seq=self._last_seq,
                kind=kind,
                timestamp=datetime.now(UTC),
                payload=clean,
            )
            self._events.append(event)
            while len(self._events) > self.max_events:
                self._events.pop(0)
                self._dropped += 1
            return event

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._last_seq

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def first_available_seq(self) -> int:
        with self._lock:
            return self._events[0].seq if self._events else self._last_seq + 1

    def page(
        self, *, after_seq: int = 0, limit: int = 100
    ) -> tuple[list[RunEvent], int, int, bool]:
        """Devuelve (eventos, next_seq, first_available_seq, truncated)."""
        with self._lock:
            first_available = self._events[0].seq if self._events else self._last_seq + 1
            selected = [e for e in self._events if e.seq > after_seq][:limit]
            next_seq = selected[-1].seq if selected else max(after_seq, self._last_seq)
            truncated = after_seq < first_available - 1
            return selected, next_seq, first_available, truncated


class QueueObserver:
    """Observador que publica eventos de frontera en una cola IPC.

    Implementa `GovernanceObserver`. Nunca lanza: si la cola falla, la
    degradación se registra y la decisión de negocio no cambia.
    """

    def __init__(self, queue: Any, run_id: str, *, max_event_bytes: int = 2048):
        self._queue = queue
        self._run_id = run_id
        self._max_event_bytes = max_event_bytes

    def on_governance_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind not in EVENT_KINDS:
            return
        message = {
            "type": "event",
            "kind": kind,
            "payload": sanitize_payload(payload, max_bytes=self._max_event_bytes),
        }
        try:
            self._queue.put(message, timeout=1.0)
        except Exception:  # noqa: BLE001 - observación degradada, no cambia la decisión
            return None
