"""
NewsTool: busca noticias financieras con GDELT como proveedor primario y
DuckDuckGo como fallback (R1).

Contrato público (se conserva): `query` (str) y `max_results` (int, 1..3).
Formato de salida: lista numerada de noticias con fecha, título, fuente y URL.

Política de fallos (R1):
- Hasta 3 reintentos adicionales al inicial por proveedor (máx. 4 intentos).
- Respuesta válida con noticias: devolver inmediatamente, sin mezclar proveedores.
- Resultado vacío válido: pasar al siguiente proveedor sin retry.
- Timeout/rate limit/fallo transitorio: retry con backoff acotado; al agotar, fallback.
- Fallo permanente/config inválida/malformada: sin retry; fallback solo si permanece autorizado.
- Fallo de gobernanza/transformación/allowlist: abortar; nunca fallback.
- Ambos vacíos válidos: informar sin noticias.
- Vacío + proveedor fallido: informar búsqueda incompleta/error seguro. No inventar resultados.
"""
from typing import Any

from pydantic import BaseModel, Field

from argentgob.module_a.governed_tool import GovernedTool
from argentgob.tools.news_providers import (
    DuckDuckGoNewsProvider,
    GDELTNewsProvider,
    MAX_ATTEMPTS_PER_PROVIDER,
    NewsItem,
    NewsProvider,
    ProviderError,
    ProviderErrorKind,
    compute_backoff_delay,
    sleep_fn,
)


class NewsToolSchema(BaseModel):
    query: str = Field(description="Ticker o nombre del activo a buscar")
    max_results: int = Field(default=3, ge=1, le=3, description="Cantidad de noticias")


class NewsTool(GovernedTool):
    """Busca noticias financieras recientes: GDELT primario, DuckDuckGo fallback."""

    name: str = "news"
    args_schema: type[BaseModel] = NewsToolSchema
    description: str = (
        "Busca las 3 últimas noticias financieras de una acción o criptomoneda. "
        "Input: query (str) con el ticker o nombre del activo."
    )
    operation_class: str = "EXTERNAL_SEND"
    resource: str = "news_providers"

    # Campos de inyección de proveedores (para tests). Se reasignan en __init__.
    primary: Any = None
    fallback: Any = None
    max_attempts: int = MAX_ATTEMPTS_PER_PROVIDER

    def __init__(
        self,
        primary: NewsProvider | None = None,
        fallback: NewsProvider | None = None,
        max_attempts: int = MAX_ATTEMPTS_PER_PROVIDER,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.primary = primary or GDELTNewsProvider()
        self.fallback = fallback or DuckDuckGoNewsProvider()
        self.max_attempts = max_attempts

    def _execute(self, query: str, max_results: int = 3) -> str:
        """Orquesta GDELT -> DDG con retries acotados y backoff."""
        query = query.strip()  # Normalización mínima: solo strip de espacios.

        # 1. Proveedor primario (GDELT).
        primary_outcome = self._search_with_retries(
            self.primary, query, max_results
        )
        if primary_outcome is not None:
            return primary_outcome

        # 2. Fallback (DuckDuckGo).
        fallback_outcome = self._search_with_retries(
            self.fallback, query, max_results
        )
        if fallback_outcome is not None:
            return fallback_outcome

        # 3. Ambos proveedores fallaron o quedaron vacíos.
        return (
            "No se pudieron obtener noticias de los proveedores disponibles. "
            "No repitas esta herramienta; continúa con los datos disponibles y "
            "declara la limitación."
        )

    def _search_with_retries(
        self, provider: NewsProvider, query: str, max_results: int
    ) -> str | None:
        """Busca con retries acotados. Retorna texto si hay resultado; None si falla."""
        for attempt in range(self.max_attempts):
            try:
                items = provider.search(query, max_results=max_results)
            except ProviderError as exc:
                if exc.kind == ProviderErrorKind.GOVERNANCE:
                    # Fallo de gobernanza: abortar, nunca fallback.
                    raise
                if exc.kind == ProviderErrorKind.PERMANENT:
                    # Sin retry; fallback solo si permanece autorizado.
                    log_provider_failure(provider, attempt, exc)
                    return None
                # Transitorio: retry con backoff acotado.
                log_provider_failure(provider, attempt, exc)
                if attempt < self.max_attempts - 1:
                    sleep_fn(compute_backoff_delay(attempt))
                continue

            if items:
                return self._format_items(provider, items)
            # Resultado vacío válido: pasar al siguiente proveedor sin retry.
            return None

        # Se agotaron los reintentos transitorios.
        return None

    def _format_items(self, provider: NewsProvider, items: list[NewsItem]) -> str:
        """Formatea las noticias en el formato de salida conservado."""
        lines = []
        for i, r in enumerate(items, 1):
            lines.append(f"{i}. [{r.date or '?'}] {r.title}")
            lines.append(f"   Fuente: {r.source} | URL: {r.url}")
            if r.body:
                lines.append(f"   {r.body[:200]}...")
            lines.append("")
        return "\n".join(lines)


def log_provider_failure(provider: NewsProvider, attempt: int, exc: ProviderError) -> None:
    """Registra un fallo de proveedor de forma sanitizada (sin query ni URLs)."""
    from argentgob.observability.logger import get_logger

    log = get_logger(__name__)
    log.warning(
        "news_provider_failure",
        provider=provider.name,
        attempt=attempt,
        kind=exc.kind.value,
    )
