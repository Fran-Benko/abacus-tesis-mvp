"""
Smoke de R5 — Logs de intentos sanitizados (news_provider_attempt).

Demuestra los logs de intentos de proveedor con correlación (event_id,
decision_id), proveedor, intento, estado y duración, y verifica que NUNCA se
filtre query, URL completa, headers, cuerpo ni excepción cruda (no-leak).

Escenarios (sin red, proveedores fake):
1. GDELT transitorio -> reintento -> éxito (TRANSIENT, SUCCESS).
2. GDELT vacío -> fallback DDG (EMPTY, SUCCESS).
3. GDELT fallo permanente -> fallback DDG (PERMANENT, SUCCESS).
4. GDELT agotado (transitorio x4) -> fallback DDG (TRANSIENT x4, SUCCESS).

El smoke NO depende de red ni de LLM: usa proveedores fake y captura los logs
con structlog.testing.capture_logs para mostrar la evidencia sanitizada.
"""
import json

import structlog
from structlog.testing import capture_logs

from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.observability.logger import setup_logging
from argentgob.tools.news_providers import (
    NewsItem,
    ProviderError,
    ProviderErrorKind,
)
from argentgob.tools.news_tool import NewsTool

setup_logging("INFO")


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


def _envelope() -> ToolCallEnvelope:
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


def _run_scenario(title: str, fn) -> None:
    print(f"\n=== {title} ===")
    with capture_logs() as logs:
        fn()
    attempts = [e for e in logs if e.get("event") == "news_provider_attempt"]
    print(f"Intentos registrados: {len(attempts)}")
    for entry in attempts:
        # Evidencia sanitizada: solo campos permitidos.
        print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    # No-leak: verificar que ningún intento filtre campos prohibidos.
    for entry in attempts:
        forbidden = {"query", "url", "headers", "body", "exception", "exc_info", "error"}
        leaked = forbidden & set(entry.keys())
        assert not leaked, f"NO-LEAK VIOLADO: {leaked} en {entry}"
    print("No-leak OK: sin query, URL, headers, body ni excepción cruda.")


def main() -> None:
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "timeout")
    permanent = ProviderError(ProviderErrorKind.PERMANENT, "malformed json")

    # 1. GDELT transitorio -> reintento -> éxito.
    def esc1():
        tool = NewsTool(
            primary=_FakeProvider("gdelt", [transient, _items("Recuperado")]),
            fallback=_FakeProvider("duckduckgo", []),
            max_attempts=4,
        )
        tool._current_envelope = _envelope()
        tool._execute(query="AAPL")

    _run_scenario("1. GDELT transitorio -> reintento -> exito", esc1)

    # 2. GDELT vacío -> fallback DDG.
    def esc2():
        tool = NewsTool(
            primary=_FakeProvider("gdelt", [[]]),
            fallback=_FakeProvider("duckduckgo", [_items("Noticia DDG")]),
            max_attempts=4,
        )
        tool._current_envelope = _envelope()
        tool._execute(query="AAPL")

    _run_scenario("2. GDELT vacio -> fallback DDG", esc2)

    # 3. GDELT fallo permanente -> fallback DDG.
    def esc3():
        tool = NewsTool(
            primary=_FakeProvider("gdelt", [permanent]),
            fallback=_FakeProvider("duckduckgo", [_items("Noticia DDG")]),
            max_attempts=4,
        )
        tool._current_envelope = _envelope()
        tool._execute(query="AAPL")

    _run_scenario("3. GDELT permanente -> fallback DDG", esc3)

    # 4. GDELT agotado (transitorio x4) -> fallback DDG.
    def esc4():
        tool = NewsTool(
            primary=_FakeProvider("gdelt", [transient] * 4),
            fallback=_FakeProvider("duckduckgo", [_items("Noticia DDG")]),
            max_attempts=4,
        )
        tool._current_envelope = _envelope()
        tool._execute(query="AAPL")

    _run_scenario("4. GDELT agotado -> fallback DDG", esc4)

    print("\nSmoke R5 completo: logs de intentos sanitizados OK.")


if __name__ == "__main__":
    main()