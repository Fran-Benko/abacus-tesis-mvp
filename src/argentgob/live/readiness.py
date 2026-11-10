"""Readiness del stack para admitir runs (AV-02).

`/ready` no debe confundir "proceso vivo" con "stack listo". Este módulo
comprueba, con timeout corto y sin imprimir credenciales:

- base alcanzable y en la revisión esperada (`alembic_version`);
- catálogo de políticas sembrado (`governance_policies > 0`);
- artefactos del predictor legibles (informados como capacidad, no como
  requisito de admisión).

El resultado se cachea unos segundos para que el healthcheck no golpee la DB en
cada sondeo. Un fallo del inicializador (migración/seed) deja `ready=False` y
la API responde 503 al admitir runs (T-A04).
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argentgob.observability.logger import get_logger

log = get_logger(__name__)

EXPECTED_HEAD = "010"
DEFAULT_CACHE_SECONDS = 5.0


@dataclass
class ReadinessReport:
    """Estado de dependencias del stack (sin secretos)."""

    ready: bool
    checks: dict[str, bool] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)


class StackReadiness:
    """Comprueba DB/esquema/catálogo con caché acotada."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any] | None = None,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._connection_factory = connection_factory
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: ReadinessReport | None = None
        self._cached_at = 0.0

    def check(self, *, force: bool = False) -> ReadinessReport:
        """Retorna el estado actual (cacheado salvo `force`)."""
        with self._lock:
            now = self._clock()
            if (
                not force
                and self._cached is not None
                and now - self._cached_at < self._cache_seconds
            ):
                return self._cached
            report = self._probe()
            self._cached = report
            self._cached_at = now
            return report

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    # ── Sonda ────────────────────────────────────────────────────────
    def _probe(self) -> ReadinessReport:
        checks: dict[str, bool] = {}
        detail: dict[str, Any] = {}
        try:
            conn = self._factory()()
        except Exception as exc:  # noqa: BLE001 - dependencia ausente es un dato
            log.warning("readiness_db_unavailable", error_type=type(exc).__name__)
            return ReadinessReport(
                ready=False,
                checks={"db_reachable": False},
                capabilities=self._capabilities(),
                detail={"db_error": type(exc).__name__},
            )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version")
                row = cur.fetchone()
                revision = (row[0] if row else "") or ""
                cur.execute("SELECT count(*) FROM governance_policies")
                policies = int(cur.fetchone()[0])
            checks["db_reachable"] = True
            checks["schema_en_head"] = revision == EXPECTED_HEAD
            checks["catalogo_sembrado"] = policies > 0
            detail["revision"] = revision
            detail["policies"] = policies
        except Exception as exc:  # noqa: BLE001 - esquema incompleto
            log.warning("readiness_schema_error", error_type=type(exc).__name__)
            checks["db_reachable"] = True
            checks["schema_en_head"] = False
            checks["catalogo_sembrado"] = False
            detail["schema_error"] = type(exc).__name__
        finally:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001 - cierre best-effort
                log.warning("readiness_close_failed", error_type=type(exc).__name__)
        return ReadinessReport(
            ready=all(checks.values()),
            checks=checks,
            capabilities=self._capabilities(),
            detail=detail,
        )

    def _factory(self) -> Callable[[], Any]:
        if self._connection_factory is not None:
            return self._connection_factory
        from argentgob.db.connection import get_connection

        return get_connection

    @staticmethod
    def _capabilities() -> dict[str, bool]:
        """Capacidades informativas: no bloquean la admisión por sí solas."""
        model_dir = Path(
            os.environ.get("RISK_MODEL_DIR")
            or Path(__file__).resolve().parents[3]
            / "data"
            / "models"
            / "risk_model_v1"
        )
        return {
            "llm_base_configured": bool(os.environ.get("OPENAI_API_BASE")),
            "llm_reachable": llm_reachable(),
            "predictor_artifacts_present": (model_dir / "pipeline.joblib").exists(),
        }


def llm_reachable(timeout: float = 1.0) -> bool:
    """True si el endpoint del LLM acepta conexion TCP (sin inferir).

    AV-02: sin LLM el servicio sigue exponiendo diagnostico, auditoria y
    dashboard, pero no admite runs que lo requieran (contrato 4).
    """
    base = os.environ.get("OPENAI_API_BASE", "")
    if not base:
        return False
    parsed = urllib.parse.urlparse(base)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
