"""Registros durables y de un solo uso de H6.

- `ApprovalRequest`: fila de `approval_requests` (PENDING/APPROVED/...).
- `ExecutionCapability`: credencial de un solo uso emitida tras un resume
  exitoso. Nunca se persiste; vive en memoria por el tiempo que dure la
  ejecucion gobernada que va a autorizar.

Estos objetos no tocan la base de datos: son la representacion en memoria que
usan los servicios transaccionales (`ApprovalService`, `GovernedResumeService`)
y la capa de ejecucion (`ExecutionOrchestrator`).
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ApprovalRequest:
    """Representacion de una solicitud de aprobacion humana."""

    approval_id: str
    event_id: str
    decision_id: str | None
    agent_id: str | None
    tool_name: str
    argument_digest: str
    idempotency_key: str
    state: str = "PENDING"
    argued_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    decision: str | None = None


@dataclass
class ExecutionCapability:
    """Capacidad de un solo uso concedida por un resume aprobado.

    `capability_id` es unico; `expires_at` acota la ventana de ejecucion;
    `consumed` evita que un mismo resume autorice dos ejecuciones.
    """

    capability_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str | None = None
    approval_id: str | None = None
    tool_name: str | None = None
    argument_digest: str | None = None
    expires_at: datetime | None = None
    consumed: bool = False
    created_at: datetime = field(default_factory=_utcnow)

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or _utcnow()) > self.expires_at

    def claim(self) -> bool:
        """Intenta consumir esta capacidad.

        Devuelve True la primera vez; False si ya fue consumida o esta vencida
        (aisla la re-ejecucion: un resume no autoriza dos efectos).
        """
        if self.consumed or self.is_expired():
            return False
        self.consumed = True
        return True