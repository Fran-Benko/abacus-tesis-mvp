"""Tests del contrato de eventos y del buffer acotado (AV-01, T-B05/T-B06/T-B07)."""
from __future__ import annotations

import json

import pytest

from argentgob.live.events import (
    EVENT_KINDS,
    EventBuffer,
    QueueObserver,
    RunEvent,
    sanitize_payload,
    strip_unsafe_text,
)


def test_kind_fuera_de_allowlist_se_descarta():
    """Un `kind` desconocido no produce evento (allowlist cerrada)."""
    buffer = EventBuffer()
    assert buffer.append("run-1", "stdout.line", {"message": "hola"}) is None
    assert buffer.page()[0] == []


def test_seq_es_monotonica_y_por_run():
    """`seq` crece de a uno y el cursor no repite eventos."""
    buffer = EventBuffer()
    for _ in range(3):
        buffer.append("run-1", "tool.requested", {"tool": "news"})
    events, next_seq, first_available, truncated = buffer.page(after_seq=0, limit=10)
    assert [e.seq for e in events] == [1, 2, 3]
    assert next_seq == 3
    assert first_available == 1
    assert truncated is False

    page2, next2, _, _ = buffer.page(after_seq=2, limit=10)
    assert [e.seq for e in page2] == [3]
    assert next2 == 3


def test_anillo_acotado_marca_truncado():
    """Al superar `max_events` se descartan los más viejos y se informa."""
    buffer = EventBuffer(max_events=3)
    for _ in range(5):
        buffer.append("run-1", "tool.requested", {"tool": "news"})
    events, _, first_available, truncated = buffer.page(after_seq=0, limit=10)
    assert [e.seq for e in events] == [3, 4, 5]
    assert first_available == 3
    assert truncated is True
    assert buffer.dropped == 2


def test_payload_fuera_de_allowlist_no_se_publica():
    """Argumentos operativos y campos desconocidos nunca llegan al evento."""
    clean = sanitize_payload(
        {
            "tool": "news",
            "query": "AAPL secreto",
            "execution_arguments": {"query": "AAPL"},
            "prompt": "system prompt",
            "message": "ok",
        },
        max_bytes=2048,
    )
    assert clean == {"tool": "news", "message": "ok"}


def test_payload_excedido_se_acota_sin_raw():
    """Un resumen enorme se recorta y se marca truncado."""
    clean = sanitize_payload(
        {"tool": "news", "result_summary": "x" * 5000}, max_bytes=512
    )
    assert "result_summary" not in clean
    assert len(json.dumps(clean).encode()) <= 512


def test_strip_unsafe_text_quita_ansi_y_controles():
    """La salida segura elimina ANSI y caracteres de control (T-P05)."""
    assert strip_unsafe_text("\x1b[31mrojo\x1b[0m\x00ok", max_chars=50) == "rojook"


def test_evento_serializa_campos_obligatorios():
    """Todo evento publica schema_version, run_id, seq, timestamp y kind."""
    buffer = EventBuffer()
    event = buffer.append("run-9", "run.started", {"state": "RUNNING"})
    assert isinstance(event, RunEvent)
    data = event.to_dict()
    for field in ("schema_version", "run_id", "seq", "timestamp", "kind"):
        assert field in data
    assert data["schema_version"] == "1"
    assert data["run_id"] == "run-9"


def test_queue_observer_envuelve_mensaje_tipado():
    """El observador de cola publica mensajes `event` con payload acotado."""
    class _Queue:
        def __init__(self):
            self.items = []

        def put(self, item, timeout=None):
            self.items.append(item)

    queue = _Queue()
    observer = QueueObserver(queue, "run-1")
    observer.on_governance_event("governance.decided", {"tool": "news", "action": "PASS"})
    assert queue.items[0]["type"] == "event"
    assert queue.items[0]["kind"] == "governance.decided"
    assert queue.items[0]["payload"] == {"tool": "news", "action": "PASS"}


def test_queue_observer_no_propaga_fallo_de_cola():
    """Una cola caída no rompe el enforcement (observación degradada)."""
    class _BrokenQueue:
        def put(self, item, timeout=None):
            raise RuntimeError("cola cerrada")

    observer = QueueObserver(_BrokenQueue(), "run-1")
    assert observer.on_governance_event("tool.requested", {"tool": "news"}) is None


def test_kinds_del_contrato_presentes():
    """El catálogo de eventos cubre los tipos exigidos por el contrato."""
    required = {
        "run.accepted",
        "run.started",
        "tool.requested",
        "governance.decided",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "approval.pending",
        "run.finished",
    }
    assert required <= EVENT_KINDS


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_paginacion_respeta_limite(limit):
    """La página nunca excede el límite solicitado."""
    buffer = EventBuffer(max_events=50)
    for _ in range(10):
        buffer.append("run-1", "tool.requested", {"tool": "news"})
    events, _, _, _ = buffer.page(after_seq=0, limit=limit)
    assert len(events) == min(limit, 10)
