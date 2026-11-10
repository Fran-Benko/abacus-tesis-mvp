"""Cliente HTTP del dashboard hacia la API interna de runs (AV-03).

Contrato: [contratos.md] §3. El dashboard no construye el DSN del agente ni
reutiliza credenciales de base de datos: usa el token de servicio montado y la
identidad del operador como cabecera de actor.

Reglas de frontera:

- Los errores se traducen a mensajes seguros y acotados; nunca se reenvía el
  cuerpo de la respuesta al usuario ni se registra el payload enviado.
- Sin token configurado el cliente no inventa credenciales: la API responde
  401/503 y la UI lo informa como dependencia no disponible.
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_BASE_URL = "http://agent:8090"
MAX_EVENTS_PER_PAGE = 100

# Mensajes seguros por código HTTP. Un código no listado usa el genérico.
_SAFE_MESSAGES: dict[int, str] = {
    400: "La API rechazó la solicitud por formato inválido.",
    401: "La API rechazó la autenticación del servicio.",
    403: "La API rechazó la operación por permisos.",
    404: "El run no existe o no es visible para este actor.",
    409: "Cupo de ejecución ocupado o intención en conflicto.",
    410: "El run expiró y ya no está disponible.",
    413: "La solicitud excede el tamaño permitido.",
    422: "Los datos de la solicitud no son válidos.",
    503: "El servicio no está listo para admitir runs.",
}

_GENERIC_ERROR = "La API no pudo completar la operación."
_UNAVAILABLE = "API no disponible. Verificá el servicio y reintentá."


class LiveApiError(Exception):
    """Error de la API con mensaje seguro (sin eco de payload ni de traza)."""

    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message

    @property
    def unavailable(self) -> bool:
        """True si el fallo fue de transporte (API caída o inalcanzable)."""
        return self.status is None


def _service_token() -> str | None:
    """Token de servicio desde archivo montado o variable de entorno."""
    from argentgob.db.dsn import read_secret_file

    from_file = read_secret_file(os.environ.get("AGENT_API_SERVICE_TOKEN_FILE"))
    if from_file is not None:
        return from_file
    return os.environ.get("AGENT_API_SERVICE_TOKEN") or None


class LiveApiClient:
    """Cliente mínimo de `POST /runs`, `GET /runs/{id}` y `GET /ready`."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        service_token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: Any | None = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._token = service_token
        self._timeout = timeout
        self._session = session if session is not None else requests.Session()

    # ── Transporte ───────────────────────────────────────────────────
    def _headers(self, actor: str | None) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if actor:
            headers["x-argentgob-actor"] = actor
        if self._token:
            headers["x-argentgob-service-token"] = self._token
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        actor: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(actor),
                json=json_body,
                params=params,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise LiveApiError(None, _UNAVAILABLE) from None

        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise LiveApiError(status, _SAFE_MESSAGES.get(status, _GENERIC_ERROR))
        try:
            payload = response.json()
        except ValueError:
            raise LiveApiError(status, "Respuesta inválida de la API.") from None
        if not isinstance(payload, dict):
            raise LiveApiError(status, "Respuesta inválida de la API.")
        return payload

    # ── Operaciones ──────────────────────────────────────────────────
    def ready(self) -> dict[str, Any]:
        """Estado del stack; no requiere actor."""
        return self._request("GET", "/ready")

    def create_run(
        self,
        *,
        actor: str,
        scenario_id: str,
        profile: str,
        asset: str,
        request_text: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Admite un run; la clave de idempotencia evita duplicar el proceso."""
        body: dict[str, Any] = {
            "scenario_id": scenario_id,
            "profile": profile,
            "input": {"asset": asset},
            "idempotency_key": idempotency_key,
        }
        clean_request = (request_text or "").strip()
        if clean_request:
            body["input"]["request"] = clean_request
        return self._request("POST", "/runs", actor=actor, json_body=body)

    def get_run(
        self,
        run_id: str,
        *,
        actor: str,
        after_seq: int = 0,
        limit: int = MAX_EVENTS_PER_PAGE,
    ) -> dict[str, Any]:
        """Snapshot de estado + página de eventos por cursor."""
        return self._request(
            "GET",
            f"/runs/{run_id}",
            actor=actor,
            params={"after_seq": max(0, int(after_seq)), "limit": int(limit)},
        )


def default_client() -> LiveApiClient:
    """Cliente configurado por entorno (`AGENT_API_BASE` + token montado)."""
    return LiveApiClient(
        base_url=os.environ.get("AGENT_API_BASE", DEFAULT_BASE_URL),
        service_token=_service_token(),
    )
