"""
Proveedores de noticias para la tool `news` (R1).

Contrato de proveedores:
- `search(query, max_results) -> list[NewsItem]`: retorna noticias o lanza
  `ProviderError` con una clasificación de fallo.
- Clasificación de fallos (matriz R1):
    * válida: lista no vacía de noticias parseadas.
    * vacía: lista vacía (resultado válido sin noticias).
    * transitoria: timeout, rate limit o fallo transitorio reconocido -> retry.
    * permanente: fallo permanente, configuración inválida o respuesta malformada
      -> sin retry; fallback solo si permanece autorizado.
    * gobernanza: fallo de gobernanza/transformación/allowlist -> abortar, nunca fallback.

GDELT es el proveedor primario; DuckDuckGo es el fallback. Google News queda
fuera del MVP. Los hosts exactos de los proveedores se autorizan como capa
interna de la tool; no se descubren ni autorizan hosts nuevos automáticamente.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import requests

from argentgob.observability.logger import get_logger

log = get_logger(__name__)

# Hosts exactos autorizados para los proveedores de noticias (R1).
GDELT_HOST = "api.gdeltproject.org"
DDG_HOST = "duckduckgo.com"

# Endpoint fijo HTTPS de GDELT 2.0 Doc API (sin URLs arbitrarias del agente).
GDELT_DOC_ENDPOINT = f"https://{GDELT_HOST}/api/v2/doc/doc"

# Presupuesto de reintentos por proveedor: 1 inicial + hasta 3 reintentos = 4.
MAX_ATTEMPTS_PER_PROVIDER = 4
# Backoff exponencial base 0.5 s con tope 4 s (propuesta inicial calibrable).
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 4.0
# Timeout por intento (propuesta inicial calibrable).
REQUEST_TIMEOUT_SECONDS = 10.0


class ProviderErrorKind(str, Enum):
    """Clasificación de fallos de un proveedor (matriz R1)."""

    TRANSIENT = "TRANSIENT"  # timeout, rate limit, fallo transitorio -> retry
    PERMANENT = "PERMANENT"  # fallo permanente, config inválida, malformada
    GOVERNANCE = "GOVERNANCE"  # fallo de gobernanza/transformación/allowlist


class ProviderError(Exception):
    """Error de un proveedor con clasificación para la política de fallos."""

    def __init__(self, kind: ProviderErrorKind, message: str):
        self.kind = kind
        super().__init__(message)


@dataclass
class NewsItem:
    """Noticia normalizada devuelta por un proveedor."""

    title: str
    url: str
    source: str
    date: str | None = None
    body: str = ""


class NewsProvider(Protocol):
    """Contrato de un proveedor de noticias."""

    name: str

    def search(self, query: str, max_results: int) -> list[NewsItem]:
        """Busca noticias. Lanza ProviderError con clasificación."""
        ...


class GDELTNewsProvider:
    """Proveedor GDELT 2.0 Doc API (primario)."""

    name: str = "gdelt"

    def __init__(
        self,
        endpoint: str = GDELT_DOC_ENDPOINT,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ):
        self.endpoint = endpoint
        self.timeout = timeout
        self.session = session or requests.Session()
        # Deshabilitar redirects: no se aceptan destinos no autorizados.
        self.session.max_redirects = 0

    def search(self, query: str, max_results: int) -> list[NewsItem]:
        """Consulta GDELT y parsea las noticias. Sin red en tests: se inyecta."""
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": max_results,
            "format": "json",
        }
        try:
            resp = self.session.get(
                self.endpoint, params=params, timeout=self.timeout
            )
        except requests.Timeout as exc:
            raise ProviderError(
                ProviderErrorKind.TRANSIENT, "gdelt timeout"
            ) from exc
        except requests.ConnectionError as exc:
            raise ProviderError(
                ProviderErrorKind.TRANSIENT, "gdelt connection error"
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                ProviderErrorKind.PERMANENT, f"gdelt request error: {exc}"
            ) from exc

        if resp.status_code == 429:
            raise ProviderError(ProviderErrorKind.TRANSIENT, "gdelt rate limited")
        if resp.status_code >= 500:
            raise ProviderError(ProviderErrorKind.TRANSIENT, "gdelt server error")
        if resp.status_code != 200:
            raise ProviderError(
                ProviderErrorKind.PERMANENT, f"gdelt http {resp.status_code}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                ProviderErrorKind.PERMANENT, "gdelt malformed json"
            ) from exc

        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> list[NewsItem]:
        """Parseo mínimo de la respuesta GDELT. No inventa fechas ni URLs."""
        articles = data.get("articles", [])
        if not isinstance(articles, list):
            raise ProviderError(
                ProviderErrorKind.PERMANENT, "gdelt malformed articles"
            )
        items: list[NewsItem] = []
        for art in articles:
            if not isinstance(art, dict):
                raise ProviderError(
                    ProviderErrorKind.PERMANENT, "gdelt malformed article"
                )
            title = art.get("title")
            url = art.get("url")
            if not title or not url:
                raise ProviderError(
                    ProviderErrorKind.PERMANENT, "gdelt missing title/url"
                )
            items.append(
                NewsItem(
                    title=str(title),
                    url=str(url),
                    source=str(art.get("domain", "") or "GDELT"),
                    date=art.get("seendate"),
                    body=str(art.get("title", "")),
                )
            )
        return items


class DuckDuckGoNewsProvider:
    """Proveedor DuckDuckGo (fallback). Encapsula el cliente DDG existente."""

    name: str = "duckduckgo"

    def __init__(self, client: Any | None = None):
        # El cliente DDG se inyecta para tests; por defecto se importa bajo demanda.
        self._client = client

    def search(self, query: str, max_results: int) -> list[NewsItem]:
        """Consulta DuckDuckGo y parsea las noticias."""
        if self._client is None:
            from duckduckgo_search import DDGS

            self._client = DDGS
        try:
            with self._client() as ddgs:
                results = list(ddgs.news(query, max_results=max_results))
        except Exception as exc:  # noqa: BLE001 - error externo del proveedor
            raise ProviderError(
                ProviderErrorKind.TRANSIENT, f"duckduckgo error: {exc}"
            ) from exc
        return self._parse(results)

    def _parse(self, results: list[dict[str, Any]]) -> list[NewsItem]:
        """Parseo mínimo de resultados DDG. No inventa fechas ni URLs."""
        items: list[NewsItem] = []
        for r in results:
            if not isinstance(r, dict):
                raise ProviderError(
                    ProviderErrorKind.PERMANENT, "duckduckgo malformed result"
                )
            title = r.get("title")
            url = r.get("url")
            if not title or not url:
                raise ProviderError(
                    ProviderErrorKind.PERMANENT, "duckduckgo missing title/url"
                )
            items.append(
                NewsItem(
                    title=str(title),
                    url=str(url),
                    source=str(r.get("source", "") or "DuckDuckGo"),
                    date=r.get("date"),
                    body=str(r.get("body", "")),
                )
            )
        return items


def compute_backoff_delay(attempt: int) -> float:
    """Backoff exponencial acotado: base 0.5 s, tope 4 s. Sin jitter en R.

    `attempt` es el número de reintento (0 = primer reintento tras el inicial).
    """
    delay = BACKOFF_BASE_SECONDS * (2**attempt)
    return min(delay, BACKOFF_MAX_SECONDS)


def sleep_fn(delay: float) -> None:
    """Función de espera sustituible (para tests con reloj fake)."""
    time.sleep(delay)