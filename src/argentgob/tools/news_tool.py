"""DuckDuckGoNewsTool: retorna las 3 últimas noticias de un ticker/empresa."""
from duckduckgo_search import DDGS
from pydantic import BaseModel, Field

from argentgob.module_a.governed_tool import GovernedTool


class DuckDuckGoNewsToolSchema(BaseModel):
    query: str = Field(description="Ticker o nombre del activo a buscar")
    max_results: int = Field(default=3, ge=1, le=3, description="Cantidad de noticias")


class DuckDuckGoNewsTool(GovernedTool):
    """Busca noticias financieras recientes vía DuckDuckGo."""

    name: str = "duckduckgo_news"
    args_schema: type[BaseModel] = DuckDuckGoNewsToolSchema
    description: str = (
        "Busca las 3 últimas noticias financieras de una acción o criptomoneda. "
        "Input: query (str) con el ticker o nombre del activo."
    )
    operation_class: str = "EXTERNAL_SEND"
    resource: str = "duckduckgo.com"

    def _execute(self, query: str, max_results: int = 3) -> str:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(query, max_results=max_results))
        except Exception as exc:  # noqa: BLE001 - error externo para el agente
            if self.governance:
                self.governance.mark_provider_unavailable(self.name)
            return (
                f"No se pudieron consultar noticias para '{query}': "
                f"{type(exc).__name__}. No repitas esta herramienta; continúa "
                "con los datos disponibles y declara la limitación."
            )
        if not results:
            return "No se encontraron noticias recientes."
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. [{r.get('date', '?')}] {r.get('title', 'Sin título')}")
            lines.append(
                f"   Fuente: {r.get('source', '?')} | URL: {r.get('url', '?')}"
            )
            lines.append(f"   {r.get('body', '')[:200]}...")
            lines.append("")
        return "\n".join(lines)
