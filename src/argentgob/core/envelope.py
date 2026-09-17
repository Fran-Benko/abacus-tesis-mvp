"""
ToolCallEnvelope: estructura que pasa por todos los módulos de gobernanza.
Separación explícita entre execution_arguments y telemetry_arguments (INV-04, INV-05).
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentIdentity:
    """Identidad lógica que origina una tool call."""

    id: str
    role: str  # nombre del perfil: "analyst", "analyst_restricted", "admin"
    version: str = "1.0"


@dataclass
class ToolCallEnvelope:
    """Envelope canónico de una tool call interceptada por el PEP."""

    event_id: str
    session_id: str
    agent: AgentIdentity
    tool_name: str
    operation_class: str  # READ, WRITE, EXTERNAL_SEND, etc.
    resource: str  # Ej: "duckduckgo.com", "yahoo_finance"
    environment: str  # LOCAL, TEST, PROD
    execution_arguments: dict[str, Any]  # Argumentos reales; NUNCA loggear crudos
    telemetry_arguments: dict[str, Any]  # Versión sanitizada; solo para logs/DB
    payload_digest: str  # sha256: del JSON canónico de execution_arguments
    payload_size_bytes: int
    received_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # R2 — Frontera gobernada.
    # Argumentos efectivos que la herramienta debe usar (originales o
    # transformados según la obligación de la decisión). Se fijan en el PEP.
    effective_arguments: dict[str, Any] = field(default_factory=dict)
    # Decisión de política asociada a este envelope (se fija en el PEP).
    decision: Any = None

    def verify_digest(self) -> bool:
        """Verifica que el digest del envelope coincida con los argumentos.

        R2 — Frontera gobernada: un digest incorrecto (tampering) bloquea la
        ejecución antes del side effect.
        """
        return _canonical_digest(self.execution_arguments) == self.payload_digest

    @staticmethod
    def build(
        agent: AgentIdentity,
        tool_name: str,
        operation_class: str,
        resource: str,
        environment: str,
        execution_arguments: dict[str, Any],
    ) -> "ToolCallEnvelope":
        """Construye un envelope calculando tamaño y digest del payload."""
        size = len(json.dumps(execution_arguments, ensure_ascii=False).encode())
        digest = _canonical_digest(execution_arguments)
        return ToolCallEnvelope(
            event_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            agent=agent,
            tool_name=tool_name,
            operation_class=operation_class,
            resource=resource,
            environment=environment,
            execution_arguments=execution_arguments,
            telemetry_arguments={},  # Se llena en el Módulo B
            payload_digest=digest,
            payload_size_bytes=size,
        )


def _canonical_digest(obj: dict) -> str:
    """SHA-256 del JSON con claves ordenadas, separadores estables y UTF-8."""
    canonical = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()