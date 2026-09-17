"""
Matriz R1 de la tool `news`: contrato y negativos ejecutables.

Cubre la política de fallos del contrato:
- válida: lista no vacía -> devolver inmediatamente, sin mezclar proveedores.
- vacía: lista vacía -> pasar al siguiente proveedor sin retry.
- transitoria: timeout/rate limit -> retry con backoff acotado; al agotar, fallback.
- permanente: fallo permanente/config inválida/malformada -> sin retry; fallback.
- gobernanza: fallo de gobernanza -> abortar, nunca fallback.
- ambos vacíos: informar sin noticias.
- vacío + fallido: informar búsqueda incompleta/error seguro.
"""
import pytest

from argentgob.tools.news_providers import (
    NewsItem,
    ProviderError,
    ProviderErrorKind,
    compute_backoff_delay,
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


# --- Caso válido --------------------------------------------------------------


def test_primario_valido_devuelve_sin_consultar_fallback():
    primary = _FakeProvider("gdelt", [_items("Noticia A", "Noticia B")])
    fallback = _FakeProvider("duckduckgo", [_items("No debería usarse")])
    tool = NewsTool(primary=primary, fallback=fallback)

    result = tool._execute(query="AAPL", max_results=2)

    assert "Noticia A" in result
    assert "Noticia B" in result
    assert primary.calls == 1
    assert fallback.calls == 0


def test_primario_valido_no_mezcla_proveedores():
    """Si GDELT devuelve noticias, no se agregan resultados de DDG."""
    primary = _FakeProvider("gdelt", [_items("Solo GDELT")])
    fallback = _FakeProvider("duckduckgo", [_items("Solo DDG")])
    tool = NewsTool(primary=primary, fallback=fallback)

    result = tool._execute(query="AAPL")

    assert "Solo GDELT" in result
    assert "Solo DDG" not in result


# --- Caso vacío ---------------------------------------------------------------


def test_primario_vacio_fallback_valido():
    primary = _FakeProvider("gdelt", [[]])
    fallback = _FakeProvider("duckduckgo", [_items("Noticia DDG")])
    tool = NewsTool(primary=primary, fallback=fallback)

    result = tool._execute(query="AAPL")

    assert "Noticia DDG" in result
    assert primary.calls == 1
    assert fallback.calls == 1


def test_ambos_vacios_informa_sin_noticias():
    primary = _FakeProvider("gdelt", [[]])
    fallback = _FakeProvider("duckduckgo", [[]])
    tool = NewsTool(primary=primary, fallback=fallback)

    result = tool._execute(query="AAPL")

    assert "No se pudieron obtener noticias" in result
    assert "No repitas esta herramienta" in result


# --- Caso transitorio ---------------------------------------------------------


def test_transitorio_reintenta_y_luego_fallback():
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "timeout")
    primary = _FakeProvider("gdelt", [transient, transient, transient, transient])
    fallback = _FakeProvider("duckduckgo", [_items("Noticia tras fallback")])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    result = tool._execute(query="AAPL")

    assert "Noticia tras fallback" in result
    assert primary.calls == 4  # 1 inicial + 3 reintentos
    assert fallback.calls == 1


def test_transitorio_recupera_en_reintento():
    transient = ProviderError(ProviderErrorKind.TRANSIENT, "rate limited")
    primary = _FakeProvider("gdelt", [transient, _items("Recuperado")])
    fallback = _FakeProvider("duckduckgo", [_items("No debería usarse")])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    result = tool._execute(query="AAPL")

    assert "Recuperado" in result
    assert primary.calls == 2
    assert fallback.calls == 0


# --- Caso permanente ----------------------------------------------------------


def test_permanente_no_reintenta_y_fallback():
    permanent = ProviderError(ProviderErrorKind.PERMANENT, "malformed json")
    primary = _FakeProvider("gdelt", [permanent])
    fallback = _FakeProvider("duckduckgo", [_items("Noticia DDG")])
    tool = NewsTool(primary=primary, fallback=fallback, max_attempts=4)

    result = tool._execute(query="AAPL")

    assert "Noticia DDG" in result
    assert primary.calls == 1  # sin retry
    assert fallback.calls == 1


# --- Caso gobernanza ----------------------------------------------------------


def test_gobernanza_aborta_y_nunca_fallback():
    governance = ProviderError(ProviderErrorKind.GOVERNANCE, "allowlist denied")
    primary = _FakeProvider("gdelt", [governance])
    fallback = _FakeProvider("duckduckgo", [_items("No debería usarse")])
    tool = NewsTool(primary=primary, fallback=fallback)

    with pytest.raises(ProviderError) as exc:
        tool._execute(query="AAPL")

    assert exc.value.kind == ProviderErrorKind.GOVERNANCE
    assert primary.calls == 1
    assert fallback.calls == 0


# --- Normalización mínima -----------------------------------------------------


def test_query_se_normaliza_con_strip():
    primary = _FakeProvider("gdelt", [_items("Noticia")])
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback)

    tool._execute(query="  AAPL  ")

    assert primary.calls == 1


# --- Backoff acotado ----------------------------------------------------------


def test_backoff_exponencial_acotado():
    assert compute_backoff_delay(0) == 0.5
    assert compute_backoff_delay(1) == 1.0
    assert compute_backoff_delay(2) == 2.0
    assert compute_backoff_delay(3) == 4.0
    assert compute_backoff_delay(4) == 4.0  # tope
    assert compute_backoff_delay(10) == 4.0  # tope


# --- Formato de salida --------------------------------------------------------


def test_formato_incluye_fecha_fuente_y_url():
    primary = _FakeProvider(
        "gdelt",
        [[NewsItem(title="Título", url="https://x.com/a", source="Reuters", date="2024-01-01")]],
    )
    fallback = _FakeProvider("duckduckgo", [])
    tool = NewsTool(primary=primary, fallback=fallback)

    result = tool._execute(query="AAPL")

    assert "Título" in result
    assert "Reuters" in result
    assert "https://x.com/a" in result
    assert "2024-01-01" in result