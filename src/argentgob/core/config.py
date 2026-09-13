"""Settings con pydantic-settings. Valida al inicio de la aplicación."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global del MVP, cargada desde entorno o archivo .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM local (llama-server expone API compatible con OpenAI)
    openai_api_base: str = "http://localhost:8080/v1"
    openai_api_key: str = "local-no-key-required"
    openai_model_name: str = "llama-3.1-8b-instruct"

    # Base de datos
    database_url: str = (
        "postgresql+psycopg://argentgob_app:dev-password-local@localhost:5432/argentgob"
    )

    # Gobernanza
    environment: str = "LOCAL"
    log_level: str = "INFO"
    agent_profile: str = "analyst"

    # Límites de payload
    max_payload_bytes: int = 65536
    max_json_depth: int = 16
    max_json_nodes: int = 1000
    max_string_bytes: int = 4096
    privacy_scan_timeout_ms: int = 25
    rate_limit_calls_per_run: int = 5


@lru_cache
def get_settings() -> Settings:
    """Retorna la instancia cacheada de Settings."""
    return Settings()
