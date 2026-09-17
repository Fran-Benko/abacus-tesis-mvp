"""
R4: property tests del backoff con Hypothesis y límite de reintentos.

Cubre, sin red y con reloj/sleep fake:
- Acotado: 0 <= delay <= BACKOFF_MAX_SECONDS para cualquier attempt no negativo.
- Monótono no decreciente en attempt.
- Valor exacto hasta el tope: min(BASE * 2**attempt, MAX).
- Límite de reintentos incluyendo retries ocultos: el total de llamadas al
  proveedor nunca excede max_attempts, incluso si el proveedor falla de forma
  transitoria en todos los intentos.
"""
from unittest.mock import patch

from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from argentgob.tools.news_providers import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    MAX_ATTEMPTS_PER_PROVIDER,
    NewsItem,
    ProviderError,
    ProviderErrorKind,
    compute_backoff_delay,
)
from argentgob.tools.news_tool import NewsTool


# --- Property tests del backoff ----------------------------------------------


@given(attempt=st.integers(min_value=0, max_value=1000))
def test_backoff_acotado_para_cualquier_attempt(attempt):
    """El delay nunca es negativo ni supera el tope, para cualquier attempt."""
    delay = compute_backoff_delay(attempt)
    assert 0 <= delay <= BACKOFF_MAX_SECONDS


@given(
    a=st.integers(min_value=0, max_value=1000),
    b=st.integers(min_value=0, max_value=1000),
)
def test_backoff_monotono_no_decreciente(a, b):
    """Si attempt crece, el delay no decrece (monótono no decreciente)."""
    if a <= b:
        assert compute_backoff_delay(a) <= compute_backoff_delay(b)
    else:
        assert compute_backoff_delay(a) >= compute_backoff_delay(b)


@given(attempt=st.integers(min_value=0, max_value=1000))
def test_backoff_valor_exacto_hasta_el_tope(attempt):
    """El delay es exactamente min(BASE * 2**attempt, MAX)."""
    expected = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS)
    assert compute_backoff_delay(attempt) == expected


@given(attempt=st.integers(min_value=0, max_value=1000))
def test_backoff_es_float_no_negativo(attempt):
    """El delay es un número real no negativo (tipo float)."""
    delay = compute_backoff_delay(attempt)
    assert isinstance(delay, float)
    assert delay >= 0.0


# --- Límite de reintentos incluyendo retries ocultos -------------------------


class _TransientProvider:
    """Proveedor que siempre falla de forma transitoria (fuerza backoff)."""

    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    def search(self, query: str, max_results: int):
        self.calls += 1
        raise ProviderError(ProviderErrorKind.TRANSIENT, "timeout")


def _items(*titles: str) -> list[NewsItem]:
    return [
        NewsItem(title=t, url=f"https://example.com/{i}", source="src")
        for i, t in enumerate(titles)
    ]


@given(max_attempts=st.integers(min_value=1, max_value=10))
@hyp_settings(max_examples=50)
def test_total_llamadas_nunca_excede_max_attempts(max_attempts):
    """Aunque el proveedor falle transitoriamente siempre, no se llama de más."""
    primary = _TransientProvider("gdelt")
    fallback = _TransientProvider("duckduckgo")
    tool = NewsTool(
        primary=primary,
        fallback=fallback,
        max_attempts=max_attempts,
    )

    # Reloj/sleep fake: sin esperas reales (sin red, sin latencia).
    with patch("argentgob.tools.news_tool.sleep_fn") as fake_sleep:
        result = tool._execute(query="AAPL")

    assert primary.calls <= max_attempts
    assert fallback.calls <= max_attempts
    # Ambos agotaron sus reintentos transitorios -> mensaje seguro.
    assert "No se pudieron obtener noticias" in result


def test_limite_incluye_retries_ocultos_por_proveedor():
    """Cada proveedor se llama a lo sumo max_attempts veces (retries ocultos)."""
    max_attempts = MAX_ATTEMPTS_PER_PROVIDER
    primary = _TransientProvider("gdelt")
    fallback = _TransientProvider("duckduckgo")
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=max_attempts)

    with patch("argentgob.tools.news_tool.sleep_fn"):
        tool._execute(query="AAPL")

    assert primary.calls == max_attempts
    assert fallback.calls == max_attempts


def test_limite_con_recuperacion_no_excede_max_attempts():
    """Si el proveedor se recupera, no se llama más allá del límite."""
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "rate limited")
    primary = _FakeProvider("gdelt", [transient, _items("Recuperado")])
    fallback = _FakeProvider("duckduckgo", [_items("No debería usarse")])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    with patch("argentgob.tools.news_tool.sleep_fn"):
        result = tool._execute(query="AAPL")

    assert "Recuperado" in result
    assert primary.calls == 2
    assert fallback.calls == 0


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