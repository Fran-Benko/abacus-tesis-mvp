"""Evidencia de autenticación de la consola H8 (Unidad 2).

La evidencia contiene SOLO campos autorizados: sujeto, proveedor, instante,
método, assurance, identificador y expiración. NUNCA credenciales (ni tokens,
ni contraseñas, ni hashes de token). Se persiste/expone únicamente esta
estructura para monitoreo y auditoría.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AuthenticationEvidence:
    """Evidencia no sensible de una autenticación.

    - `subject`: identificador del actor (p. ej. "auditor-1").
    - `provider`: proveedor de identidad ("fixture" en modo desarrollo).
    - `instant`: instante en que se autenticó (UTC).
    - `method`: método de autenticación ("fixture" en modo desarrollo).
    - `assurance`: nivel de assurance ("DEVELOPMENT" en modo fixture).
    - `identifier`: identificador estable del actor.
    - `expires_at`: expiración de la sesión/evidencia.
    - `development_only`: True si proviene del modo fixture de desarrollo.
    """

    subject: str
    provider: str
    method: str
    assurance: str
    identifier: str
    instant: datetime = field(default_factory=_utcnow)
    expires_at: datetime | None = None
    development_only: bool = False

    def is_expired(self, now: datetime | None = None) -> bool:
        """True si la evidencia venció (expires_at en el pasado)."""
        if self.expires_at is None:
            return False
        now = now or _utcnow()
        return now > self.expires_at

    def to_dict(self) -> dict:
        """Serializa la evidencia (solo campos autorizados, sin credenciales)."""
        return {
            "subject": self.subject,
            "provider": self.provider,
            "instant": self.instant.isoformat(),
            "method": self.method,
            "assurance": self.assurance,
            "identifier": self.identifier,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "development_only": self.development_only,
        }