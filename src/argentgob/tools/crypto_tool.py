"""CryptoPriceTool: precio, market cap y variación 24h vía CoinGecko (API gratuita)."""
import requests
from pydantic import BaseModel, Field

from argentgob.module_a.governed_tool import GovernedTool

COINGECKO_API = "https://api.coingecko.com/api/v3"


class CryptoPriceToolSchema(BaseModel):
    coin_id: str = Field(description="Identificador de CoinGecko, por ejemplo bitcoin")


class CryptoPriceTool(GovernedTool):
    """Consulta precios de criptomonedas en la API pública de CoinGecko."""

    name: str = "crypto_price"
    args_schema: type[BaseModel] = CryptoPriceToolSchema
    description: str = (
        "Obtiene precio USD, market cap y variación 24h de una criptomoneda. "
        "Input: coin_id (str) en formato CoinGecko, Ej: 'bitcoin', 'ethereum', 'cardano'"
    )
    operation_class: str = "READ"
    resource: str = "coingecko.com"

    def _execute(self, coin_id: str) -> str:
        coin_id = coin_id.lower().strip()
        url = f"{COINGECKO_API}/simple/price"
        params = {
            "ids": coin_id,
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_change": "true",
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json().get(coin_id, {})
            if not data:
                return f"Criptomoneda '{coin_id}' no encontrada en CoinGecko."
            price = data.get("usd", 0)
            cap = data.get("usd_market_cap", 0)
            change = data.get("usd_24h_change", 0)
            return (
                f"🪙 {coin_id.upper()}\n"
                f"Precio: ${price:,.4f} USD\n"
                f"Market Cap: ${cap:,.0f} USD\n"
                f"Variación 24h: {change:+.2f}%"
            )
        except Exception as e:  # noqa: BLE001 - error informativo para el agente
            return f"Error al consultar CoinGecko: {e}"
