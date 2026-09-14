"""Contratos de argumentos publicados a CrewAI para las herramientas."""
from pydantic import ValidationError
import pytest

from argentgob.tools.crypto_tool import CryptoPriceToolSchema
from argentgob.tools.news_tool import DuckDuckGoNewsToolSchema
from argentgob.tools.stock_tool import StockPriceToolSchema
from argentgob.agent.crew import build_analysis_crew


def test_stock_schema_requires_ticker():
    assert StockPriceToolSchema(ticker="AAPL").ticker == "AAPL"
    with pytest.raises(ValidationError):
        StockPriceToolSchema()


def test_news_schema_has_bounded_default():
    schema = DuckDuckGoNewsToolSchema(query="AAPL")
    assert schema.max_results == 3
    with pytest.raises(ValidationError):
        DuckDuckGoNewsToolSchema(query="AAPL", max_results=4)


def test_crypto_schema_requires_coin_id():
    assert CryptoPriceToolSchema(coin_id="bitcoin").coin_id == "bitcoin"
    with pytest.raises(ValidationError):
        CryptoPriceToolSchema()


def test_crewai_usa_parser_cliente_para_llama_local(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused")
    crew = build_analysis_crew("AAPL")
    assert crew.agents[0].llm.supports_function_calling() is False
    assert crew.agents[0].max_iter == 5
    assert crew.agents[0].max_retry_limit == 0
