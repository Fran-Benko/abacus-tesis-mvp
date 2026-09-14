"""Comportamiento de la herramienta de noticias ante fallos del proveedor."""
from unittest.mock import MagicMock

from argentgob.tools.news_tool import DuckDuckGoNewsTool


def test_news_provider_error_se_entrega_como_observacion(monkeypatch):
    provider = MagicMock()
    provider.__enter__.side_effect = RuntimeError("rate limited")
    monkeypatch.setattr("argentgob.tools.news_tool.DDGS", lambda: provider)

    result = DuckDuckGoNewsTool()._execute(query="AAPL")

    assert "RuntimeError" in result
    assert "No repitas esta herramienta" in result