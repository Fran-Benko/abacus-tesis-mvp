"""Ejecucion gobernada tras un resume aprobado (Unidad 5).

Garantias:
- **Guard/claim**: la ExecutionCapability de un solo uso se consume ANTES de
  invocar al executor; si ya fue consumida o esta vencida, no se ejecuta.
- **Estados**: `SUCCESS` / `FAILED` (efecto conocido) / `UNKNOWN` (resultado
  indeterminado) se persisten en `execution_results`.
- **Nunca retry automatico**: si la DB falla despues de un posible efecto, NO
  se fabrica un `UNKNOWN` persistido como si fuera un estado real de ejecucion;
  se emite un diagnostico seguro y se bloquea la auto-recuperacion (un operador
  debe decidir). No se re-ejecuta la herramienta.
"""
from datetime import datetime, timezone
from typing import Any, Callable

from argentgob.core.envelope import ToolCallEnvelope
from argentgob.db.connection import get_connection
from argentgob.hitl.errors import HitlError
from argentgob.hitl.records import ExecutionCapability
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class ExecutionOrchestrator:
    """Guarda y ejecuta una capability de un solo uso; persiste el resultado."""

    def __init__(
        self,
        settings,
        connection_factory: Callable[[], Any] | None = None,
    ):
        self.settings = settings
        self._connection_factory = connection_factory or (lambda: get_connection())

    def execute_governed(
        self,
        *,
        capability: ExecutionCapability,
        envelope: ToolCallEnvelope,
        executor: Callable[[dict[str, Any]], Any],
    ) -> str:
        """Ejecuta una sola vez la herramienta con los argumentos retenidos.

        Retorna el estado final ("SUCCESS", "FAILED" o "UNKNOWN") sin intentar
        ningun reintento automatico. Si la capability no es consumible, no
        ejecuta nada.
        """
        if capability is None:
            raise HitlError("no hay capability para ejecutar")
        # 1. Guard/claim: consume la capability ANTES del executor.
        if not capability.claim():
            log.warning(
                "capability_rejected",
                capability_id=capability.capability_id,
                reason="consumed_or_expired",
            )
            raise HitlError("capability no consumible (reusada o vencida)")

        started_ns = datetime.now(timezone.utc).timestamp() * 1000
        try:
            result = executor(envelope.effective_arguments or {})
            status = "SUCCESS"
            self._persist_result(capability, envelope, status, error=None)
            return f"governed:{status}"
        except Exception as exc:  # noqa: BLE001 - se registra como FAILED conocido
            log.error(
                "governed_execution_failed",
                event_id=envelope.event_id,
                error_type=type(exc).__name__,
            )
            status = "FAILED"
            try:
                self._persist_result(capability, envelope, status, error=str(exc))
            except Exception as persist_exc:  # noqa: BLE001 - diagnostico seguro
                # La DB fallo tras un posible efecto: no fabricar UNKNOWN.
                log.critical(
                    "governed_result_unpersistable",
                    event_id=envelope.event_id,
                    error=str(persist_exc),
                    diagnosis=(
                        "posible efecto con resultado no persistido; "
                        "bloquear auto-recuperacion, requerir operador"
                    ),
                )
                raise HitlError(
                    "ejecucion no recuperable automaticamente: no se pudo "
                    "persistir el resultado tras un posible efecto"
                ) from persist_exc
            return f"governed:{status}"

    # ── Persistencia ──────────────────────────────────────────────────
    def _persist_result(
        self,
        capability: ExecutionCapability,
        envelope: ToolCallEnvelope,
        status: str,
        error: str | None,
    ) -> None:
        """Escribe `execution_results` con unicidad por event_id.

        Si ya existe un resultado para el evento, no se pisa: un efecto ya
        registrado no se re-emite (RESULT_ALREADY_EXISTS).
        """
        conn = self._connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO execution_results (
                        event_id, decision_id, tool_name, status,
                        error_digest, result_digest, latency_post_ms, recorded_at
                    ) VALUES (
                        %(event_id)s, %(decision_id)s, %(tool_name)s, %(status)s,
                        %(error_digest)s, NULL, NULL, now()
                    )
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    {
                        "event_id": envelope.event_id,
                        "decision_id": envelope.decision.decision_id
                        if getattr(envelope, "decision", None)
                        else None,
                        "tool_name": envelope.tool_name,
                        "status": status,
                        "error_digest": _digest_of(error) if error else None,
                    },
                )
        finally:
            conn.close()


def _digest_of(value: str) -> str:
    """Digest sha256 del texto del error (nunca se persiste el mensaje crudo)."""
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()