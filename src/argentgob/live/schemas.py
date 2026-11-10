"""Schemas estrictos de la API interna de runs (AV-01).

Contrato: [contratos.md] §3. JSON estricto, campos extra rechazados, sin eco de
payload crudo en errores. Solo el escenario `finanzas` está habilitado en este
hito; los cinco contextos públicos son AV-05.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Catálogo cerrado de escenarios habilitados en AV-01.
ENABLED_SCENARIOS: frozenset[str] = frozenset({"finanzas"})

# Perfiles permitidos por escenario. `admin` queda fuera de la API de runs
# (AV-I05): es un perfil de debug, no un perfil de operación.
ALLOWED_PROFILES: dict[str, frozenset[str]] = {
    "finanzas": frozenset({"analyst", "analyst_restricted"}),
}

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")
_ASSET_RE = re.compile(r"^[A-Za-z0-9 .^=_-]{1,80}$")


class FinanzasInput(BaseModel):
    """Input del escenario financiero (contrato §2 de escenarios)."""

    model_config = ConfigDict(extra="forbid")

    asset: str = Field(min_length=1, max_length=80)
    request: str | None = Field(default=None, max_length=1000)

    @field_validator("asset")
    @classmethod
    def _validate_asset(cls, value: str) -> str:
        candidate = value.strip()
        if not _ASSET_RE.match(candidate):
            raise ValueError("asset contiene caracteres no permitidos")
        return candidate

    @field_validator("request")
    @classmethod
    def _validate_request(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if any(ord(ch) < 32 and ch not in "\t\n" for ch in candidate):
            raise ValueError("request contiene caracteres de control")
        return candidate


class RunCreateRequest(BaseModel):
    """Cuerpo de `POST /runs`."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: Literal["finanzas"]
    profile: str = Field(min_length=1, max_length=64)
    input: FinanzasInput
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not _IDEMPOTENCY_RE.match(value):
            raise ValueError("idempotency_key contiene caracteres no permitidos")
        return value

    @field_validator("profile")
    @classmethod
    def _validate_profile(cls, value: str) -> str:
        allowed = ALLOWED_PROFILES.get("finanzas", frozenset())
        if value not in allowed:
            raise ValueError("profile no permitido para el escenario")
        return value


class RunAcceptedResponse(BaseModel):
    """Respuesta 202 de `POST /runs`."""

    schema_version: str = "1"
    run_id: str
    state: str
    poll_after_ms: int
    query_path: str


class RunSnapshotResponse(BaseModel):
    """Respuesta de `GET /runs/{id}` (snapshot siempre completo)."""

    schema_version: str = "1"
    run_id: str
    state: str
    events: list[dict[str, Any]]
    next_seq: int
    first_available_seq: int
    truncated: bool
    result: dict[str, Any] | None = None
    predictor_status: str = "NOT_VALIDATED_FOR_DOMAIN"


class HealthResponse(BaseModel):
    """Respuesta de `GET /health`."""

    status: str = "ok"
    service: str = "argentgob-agent-api"


class ReadyResponse(BaseModel):
    """Respuesta de `GET /ready`.

    `ready` refleja configuración mínima del servicio; las capacidades
    (LLM, cupo) se informan por separado para no confundir "proceso roto" con
    "dependencia ausente" (contrato §3).
    """

    ready: bool
    checks: dict[str, bool]
    capabilities: dict[str, bool]
    active_run: bool


def validate_actor_id(actor_id: str | None) -> str:
    """Valida el identificador de actor recibido por canal autenticado."""
    if not actor_id or not _ACTOR_RE.match(actor_id):
        raise ValueError("actor inválido")
    return actor_id
