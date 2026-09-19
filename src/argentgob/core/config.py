"""Settings con pydantic-settings. Valida al inicio de la aplicación."""
from functools import lru_cache

from pydantic import model_validator
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

    # H6 — Aprobación humana durable y ejecución gobernada.
    # Límite superior de argumentos que pueden mantenerse en el hold en
    # memoria (InMemoryArgumentHold). Positivo, obligatorio en TEST y nunca
    # superior a `max_payload_bytes` (norma H6, Unidad 1).
    max_held_argument_bytes: int = 32768
    # Cota total efectiva del hold en memoria (no acumular bytes ilimitados).
    max_held_total_bytes: int = 262144
    # TTL del hold en memoria. No supera el timeout de aprobación.
    hold_ttl_seconds: int = 300
    # TTL de una aprobación pendiente (expiración).
    approval_ttl_seconds: int = 300
    # Roles de auditor autorizados para resolver aprobaciones (Unidad 3).
    authorized_auditor_roles: list[str] = []

    @model_validator(mode="after")
    def _validate_h6_constraints(self) -> "Settings":
        """Reglas normativas H6 (Unidad 1, hold acotado)."""
        if self.max_held_argument_bytes <= 0:
            raise ValueError("max_held_argument_bytes debe ser positivo")
        if self.max_held_argument_bytes > self.max_payload_bytes:
            raise ValueError("max_held_argument_bytes no puede superar max_payload_bytes")
        if self.hold_ttl_seconds > self.approval_ttl_seconds:
            raise ValueError("hold_ttl_seconds no puede superar approval_ttl_seconds")
        if self.max_held_total_bytes < self.max_held_argument_bytes:
            raise ValueError("max_held_total_bytes no puede ser menor que max_held_argument_bytes")
        return self



@lru_cache
def get_settings() -> Settings:
    """Retorna la instancia cacheada de Settings."""
    return Settings()
