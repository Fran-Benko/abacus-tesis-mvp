"""
R5 - Logs de intentos sanitizados (news_provider_attempt).

Verifica que cada intento de proveedor se registre con correlación
(event_id/decision_id), proveedor, intento, estado y duración, y que NUNCA se
filtre query, URL completa, headers, cuerpo ni excepción cruda (no-leak).

Determinístico: usa proveedores fake y structlog.testing.capture_logs; no
depende de red ni de LLM.
"""
import structlog
from structlog.testing import capture_logs

from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.tools.news_providers import (
    NewsItem,
    ProviderError,
    ProviderErrorKind,
)
from argentgob.tools.news_tool import NewsTool


class _FakeProvider:
    """Proveedor fake con comportamiento configurable por llamada."""

    def __init__(self, name: str, script):
        self.name = name
        self._script = list(script)
        self.calls = 0

    def search(self, query: str, max_results: int):
        self.calls += 1
        if not self._script:
            raise AssertionError(f"{self.name} llamado más veces de lo esperado")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _items(*titles: str) -> list[NewsItem]:
    return [
        NewsItem(title=t, url=f"https://example.com/{i}", source="src")
        for i, t in enumerate(titles)
    ]


def _envelope_with_decision() -> ToolCallEnvelope:
    """Envelope con decisión PASS para correlacionar los intentos."""
    env = ToolCallEnvelope.build(
        agent=AgentIdentity(id="agente-analyst-001", role="analyst"),
        tool_name="news",
        operation_class="EXTERNAL_SEND",
        resource="news_providers",
        environment="TEST",
        execution_arguments={"query": "AAPL", "max_results": 3},
    )
    env.decision = PolicyDecision()
    return env


def _attempt_events(logs) -> list[dict]:
    """Filtra los eventos news_provider_attempt de los logs capturados."""
    return [e for e in logs if e.get("event") == "news_provider_attempt"]


# --- Correlación y campos sanitizados ----------------------------------------


def test_attempt_success_incluye_correlacion_y_campos():
    """Un intento exitoso loggea event_id, decision_id, provider, attempt,
    status y duration_ms."""
    primary = _FakeProvider("gdelt", [_items("Noticia A")])
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback)
    env = _envelope_with_decision()
    tool._current_envelope = env

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    assert len(attempts) == 1
    entry = attempts[0]
    assert entry["event_id"] == env.event_id
    assert entry["decision_id"] == env.decision.decision_id
    assert entry["provider"] == "gdelt"
    assert entry["attempt"] == 0
    assert entry["status"] == "SUCCESS"
    assert isinstance(entry["duration_ms"], float)


def test_attempt_sin_envelope_no_incluye_correlacion():
    """Sin envelope en curso, el intento se loggea sin event_id/decision_id."""
    primary = _FakeProvider("gdelt", [_items("Noticia A")])
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback)

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    assert len(attempts) == 1
    assert "event_id" not in attempts[0]
    assert "decision_id" not in attempts[0]


def test_attempts_por_estado_y_proveedor():
    """Cada intento registra su estado y proveedor; el fallback también."""
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "timeout")
    primary = _FakeProvider("gdelt", [transient, _items("Recuperado")])
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    assert [a["status"] for a in attempts] == ["TRANSIENT", "SUCCESS"]
    assert [a["provider"] for a in attempts] == ["gdelt", "gdelt"]
    assert [a["attempt"] for a in attempts] == [0, 1]


def test_attempts_vacio_y_fallback():
    """Vacio en primario (EMPTY) y éxito en fallback (SUCCESS)."""
    primary = _FakeProvider("gdelt", [[]])
    fallback = _FakeProvider("duckduckgo", [_items("Noticia DDG")])
    tool = NewsTool(primary=primary, fallback=fallback)

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    assert [a["status"] for a in attempts] == ["EMPTY", "SUCCESS"]
    assert [a["provider"] for a in attempts] == ["gdelt", "duckduckgo"]


def test_attempt_permanente_no_reintenta():
    """Un fallo permanente se loggea una vez (PERMANENT) sin retry."""
    permanent = ProviderError(ProviderErrorKind.PERMANENT, "malformed json")
    primary = _FakeProvider("gdelt", [permanent])
    fallback = _FakeProvider("duckduckgo", [_items("Noticia DDG")])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    assert [a["status"] for a in attempts] == ["PERMANENT", "SUCCESS"]
    assert primary.calls == 1  # sin retry


# --- No-leak: nunca se filtra query, URL, headers, body ni excepción ----------


def test_no_leak_query_url_headers_body_excepcion():
    """Los intentos nunca exponen query, URL, headers, body ni excepción cruda."""
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "timeout")
    primary = _FakeProvider("gdelt", [transient, _items("Noticia A")])
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    assert attempts  # al menos un intento
    for entry in attempts:
        # Campos prohibidos por INV-05 / R5.
        assert "query" not in entry
        assert "url" not in entry
        assert "headers" not in entry
        assert "body" not in entry
        assert "exception" not in entry
        assert "exc_info" not in entry
        assert "error" not in entry
        # Solo los campos permitidos + correlación (+ log_level de capture_logs).
        allowed = {"event", "event_id", "decision_id", "provider", "attempt",
                           "status", "duration_ms", "log_level"}
        assert set(entry.keys()) <= allowed


def test_no_leak_en_fallo_permanente():
    """Un fallo permanente tampoco filtra la excepción cruda."""
    permanent = ProviderError(ProviderErrorKind.PERMANENT, "malformed json")
    primary = _FakeProvider("gdelt", [permanent])
    fallback = _FakeProvider("duckduckgo", [_items("Noticia DDG")])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    with capture_logs() as logs:
        tool._execute(query="AAPL")

    attempts = _attempt_events(logs)
    for entry in attempts:
        assert "exception" not in entry
        assert "exc_info" not in entry
        assert "error" not in entry
        assert "query" not in entry


# --- No se crean resultados de ejecución por retry ---------------------------


def test_no_crea_resultados_por_retry():
    """Los reintentos no generan resultados de ejecución adicionales."""
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "timeout")
    primary = _FakeProvider("gdelt", [transient, _items("Recuperado")])
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    with capture_logs() as logs:
        result = tool._execute(query="AAPL")

    # Solo un resultado final (el texto formateado), no uno por retry.
    assert "Recuperado" in result
    # Los eventos de intento no son resultados de ejecución.
    assert all(e.get("event") != "tool_call_complete" for e in logs)
    # El único evento de intento exitoso es el final.
    success = [e for e in _attempt_events(logs) if e["status"] == "SUCCESS"]
    assert len(success) == 1