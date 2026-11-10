"""API HTTP interna de runs (AV-01).

Contrato: [contratos.md] §3. Un worker ASGI, JSON estricto, sin eco de payload
crudo. La identidad del actor llega por canal autenticado (cabecera de
servicio), nunca por el cuerpo.

Rutas: `POST /runs`, `GET /runs/{id}`, `GET /health`, `GET /ready`.
`POST /runs/{id}/resume` queda fuera de este hito (AV-05).
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from argentgob.live.schemas import (
    HealthResponse,
    ReadyResponse,
    RunAcceptedResponse,
    RunCreateRequest,
    RunSnapshotResponse,
    validate_actor_id,
)
from argentgob.live.supervisor import (
    DEFAULT_POLL_AFTER_MS,
    RunCapacityError,
    RunConflictError,
    RunNotFoundError,
    RunSupervisor,
)
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

ACTOR_HEADER = "x-argentgob-actor"
SERVICE_TOKEN_HEADER = "x-argentgob-service-token"
MAX_BODY_BYTES = 65536


def _service_token() -> str | None:
    """Token de servicio esperado (vacío = sin autenticación, solo LOCAL/TEST)."""
    return os.environ.get("AGENT_API_SERVICE_TOKEN") or None


def _environment() -> str:
    return (os.environ.get("ENVIRONMENT") or "LOCAL").upper()


def create_app(supervisor: RunSupervisor | None = None) -> FastAPI:
    """Construye la app ASGI con el supervisor inyectado o uno por defecto."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.supervisor.shutdown()

    app = FastAPI(
        title="ArgentGob-Mesh · Agent API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.supervisor = supervisor or RunSupervisor()

    def _require_actor(actor_header: str | None, token_header: str | None) -> str:
        expected = _service_token()
        if expected is not None:
            if token_header is None or token_header != expected:
                raise HTTPException(status_code=401, detail="autenticación inválida")
        elif _environment() not in ("LOCAL", "TEST", "DEV", "DEVELOPMENT"):
            # Sin token configurado fuera de desarrollo: fail-closed.
            raise HTTPException(status_code=503, detail="autenticación no configurada")
        try:
            return validate_actor_id(actor_header)
        except ValueError:
            raise HTTPException(status_code=401, detail="actor inválido") from None

    @app.middleware("http")
    async def _limit_body(request: Request, call_next):
        """Rechaza cuerpos demasiado grandes antes del parseo caro (T-B01)."""
        if request.method in ("POST", "PUT", "PATCH"):
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=413, content={"detail": "payload demasiado grande"}
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=400, content={"detail": "content-length inválido"}
                    )
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413, content={"detail": "payload demasiado grande"}
                )
        return await call_next(request)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Proceso HTTP vivo; no consulta el LLM."""
        return HealthResponse()

    @app.get("/ready", response_model=ReadyResponse)
    def ready() -> ReadyResponse:
        """Readiness: configuración mínima y capacidades informadas aparte."""
        checks = {
            "service_token_configured": _service_token() is not None
            or _environment() in ("LOCAL", "TEST", "DEV", "DEVELOPMENT"),
        }
        capabilities = {
            "llm_base_configured": bool(os.environ.get("OPENAI_API_BASE")),
            "capacity_free": app.state.supervisor.active_run_id is None,
        }
        return ReadyResponse(
            ready=all(checks.values()),
            checks=checks,
            capabilities=capabilities,
            active_run=app.state.supervisor.active_run_id is not None,
        )

    @app.post("/runs", status_code=202, response_model=RunAcceptedResponse)
    def create_run(
        payload: RunCreateRequest,
        actor_header: str | None = Header(default=None, alias=ACTOR_HEADER),
        token_header: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
    ) -> RunAcceptedResponse:
        """Admite un run validado; cero inferencias si la entrada es inválida."""
        actor_id = _require_actor(actor_header, token_header)
        input_payload = payload.input.model_dump(exclude_none=True)
        try:
            record, _created = app.state.supervisor.submit(
                actor_id=actor_id,
                scenario_id=payload.scenario_id,
                profile=payload.profile,
                input_payload=input_payload,
                idempotency_key=payload.idempotency_key,
            )
        except RunConflictError:
            raise HTTPException(
                status_code=409, detail="idempotency_key en conflicto"
            ) from None
        except RunCapacityError:
            raise HTTPException(status_code=409, detail="cupo de ejecución ocupado") from None
        return RunAcceptedResponse(
            run_id=record.run_id,
            state=record.state,
            poll_after_ms=DEFAULT_POLL_AFTER_MS,
            query_path=f"/runs/{record.run_id}",
        )

    @app.get("/runs/{run_id}", response_model=RunSnapshotResponse)
    def get_run(
        run_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        actor_header: str | None = Header(default=None, alias=ACTOR_HEADER),
        token_header: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
    ) -> RunSnapshotResponse:
        """Snapshot de estado + eventos por cursor; 404 uniforme si no es visible."""
        actor_id = _require_actor(actor_header, token_header)
        try:
            snapshot: dict[str, Any] = app.state.supervisor.snapshot(
                run_id, actor_id=actor_id, after_seq=after_seq, limit=limit
            )
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="run no encontrado") from None
        return RunSnapshotResponse(**snapshot)

    return app

app = create_app()
