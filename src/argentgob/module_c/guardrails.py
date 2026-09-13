"""
Guardrails del dominio financiero.
Cada guardrail retorna (passed: bool, reason_code: str | None).
Los bloqueos se muestran en consola con formato observable.
"""
import re

from argentgob.core.envelope import ToolCallEnvelope

# Patrones de inyección básicos (SQL injection y prompt injection).
_INJECTION_PATTERNS = [
    re.compile(r"(?i)(drop\s+table|delete\s+from|insert\s+into|exec\s*\()"),
    re.compile(r"[;'\"]{2,}"),
    re.compile(r"(?i)(ignore\s+previous|forget\s+instructions|you\s+are\s+now)"),
]

# Tickers y términos financieros para validar relevancia temática.
# El ticker (1-5 mayúsculas) se valida SIN ignorar mayúsculas/minúsculas para no
# matchear cualquier palabra corta; los términos temáticos sí son case-insensitive.
_FINANCIAL_PATTERN = re.compile(
    r"(\b[A-Z]{1,5}\b)"
    r"|(?i:bitcoin|ethereum|btc|eth|stock|acci[oó]n|cripto|precio|price|"
    r"market|mercado|nasdaq|nyse|sp500|dow\s*jones|[ií]ndice|analysis|an[aá]lisis)"
)


class GuardrailEngine:
    """Ejecuta los guardrails activos de un perfil sobre un envelope."""

    def __init__(self, profile_guardrails: list[str], rate_limit: int):
        self.active = profile_guardrails
        self.rate_limit = rate_limit
        self._call_count = 0

    def run_all(
        self, envelope: ToolCallEnvelope
    ) -> list[tuple[str, bool, str | None]]:
        """Retorna lista de (guardrail_name, passed, reason_code_si_bloqueado)."""
        results: list[tuple[str, bool, str | None]] = []
        for guard_name in self.active:
            guard_fn = _GUARD_REGISTRY.get(guard_name)
            if guard_fn:
                passed, reason = guard_fn(self, envelope)
                results.append((guard_name, passed, reason))
                if not passed:
                    break  # Detener en el primer bloqueo
        return results

    def _query_injection_guard(
        self, envelope: ToolCallEnvelope
    ) -> tuple[bool, str | None]:
        query = envelope.execution_arguments.get("query", "")
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(query):
                return False, "INJECTION_PATTERN_DETECTED"
        return True, None

    def _topic_relevance_guard(
        self, envelope: ToolCallEnvelope
    ) -> tuple[bool, str | None]:
        """Solo permite búsquedas con términos financieros reconocibles."""
        if envelope.tool_name != "duckduckgo_news":
            return True, None  # No aplica a herramientas de precio
        query = envelope.execution_arguments.get("query", "")
        if not _FINANCIAL_PATTERN.search(query):
            return False, "TOPIC_NOT_FINANCIAL"
        return True, None

    def _rate_limit_guard(
        self, envelope: ToolCallEnvelope
    ) -> tuple[bool, str | None]:
        self._call_count += 1
        if self._call_count > self.rate_limit:
            return False, "RATE_LIMIT_EXCEEDED"
        return True, None

    def reset_rate_limit(self) -> None:
        """Reinicia el contador de llamadas (usar entre corridas)."""
        self._call_count = 0


_GUARD_REGISTRY = {
    "query_injection": GuardrailEngine._query_injection_guard,
    "topic_relevance": GuardrailEngine._topic_relevance_guard,
    "rate_limit": GuardrailEngine._rate_limit_guard,
}
