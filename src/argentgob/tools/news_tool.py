"""DuckDuckGoNewsTool: retorna las 3 últimas noticias de un ticker/empresa."""
from duckduckgo_search import DDGS

from argentgob.module_a.governed_tool import GovernedTool


class DuckDuckGoNewsTool(GovernedTool):
    """Busca noticias financieras recientes vía DuckDuckGo."""

    name: str = "duckduckgo_news"
    description: str = (
        "Busca las 3 últimas noticias financieras de una acción o criptomoneda. "
        "Input: query (str) con el ticker o nombre del activo."
    )
    operation_class: str = "EXTERNAL_SEND"
    resource: str = "duckduckgo.com"

    def _execute(self, query: str, max_results: int = 3) -> str:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
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
