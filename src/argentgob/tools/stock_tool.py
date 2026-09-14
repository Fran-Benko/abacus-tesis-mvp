"""StockPriceTool: precio actual, variación y volumen vía Yahoo Finance."""
import yfinance as yf
from pydantic import BaseModel, Field

from argentgob.module_a.governed_tool import GovernedTool


class StockPriceToolSchema(BaseModel):
    ticker: str = Field(description="Símbolo bursátil, por ejemplo AAPL o MSFT")


class StockPriceTool(GovernedTool):
    """Obtiene datos de mercado de una acción usando yfinance."""

    name: str = "stock_price"
    args_schema: type[BaseModel] = StockPriceToolSchema
    description: str = (
        "Obtiene el precio actual, variación diaria y volumen de una acción. "
        "Input: ticker (str), Ej: 'AAPL', 'MSFT', 'GGAL.BA'"
    )
    operation_class: str = "READ"
    resource: str = "yahoo_finance"

    def _execute(self, ticker: str) -> str:
        ticker = ticker.upper().strip()
        try:
            info = yf.Ticker(ticker).fast_info
            price = info.last_price
            prev = info.previous_close
            change_pct = ((price - prev) / prev * 100) if prev else 0
            volume = info.three_month_average_volume
            return (
                f"📈 {ticker}\n"
                f"Precio: ${price:.2f}\n"
                f"Variación: {change_pct:+.2f}%\n"
                f"Volumen promedio 3M: {volume:,.0f}"
            )
        except Exception as e:  # noqa: BLE001 - error informativo para el agente
            return f"No se pudo obtener datos para '{ticker}': {e}"
