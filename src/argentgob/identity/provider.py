"""AuditorIdentityProvider: identidad y autorización de la consola H8 (Unidad 2).

Reutiliza el contrato del plan: `authenticate`, `has_role` y
`authentication_evidence`. La evidencia contiene sujeto, proveedor, instante,
método, assurance, identificador y expiración; nunca credenciales.

Modo fixture (desarrollo):
- Marcado `development_only=true`, assurance DEVELOPMENT y advertencia visible.
- NO se inventa integración productiva/MFA. Si se exigiera producción sin un
  proveedor real, se bloquea la configuración en vez de tratar el fixture como
  autenticación fuerte.

Roles de aplicación: viewer (consulta), auditor (resuelve), policy_admin
(versiona), operator (recuperación no destructiva). Resolver exige rol auditor
+ evidencia vigente en el backend, no solo ocultar el botón.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from argentgob.identity.evidence import AuthenticationEvidence
from argentgob.identity.roles import AppRole


class IdentityError(Exception):
    """Error de identidad/autenticación de la consola."""


class UnauthenticatedError(IdentityError):
    """No hay sesión autenticada."""


class UnauthorizedRoleError(IdentityError):
    """El actor no tiene el rol requerido."""


class ExpiredEvidenceError(IdentityError):
    """La evidencia de autenticación venció."""


class FixtureNotAllowedInProductionError(IdentityError):
    """Se intentó usar el modo fixture en un entorno que no es de desarrollo."""


class AuditorIdentityProvider:
    """Provee identidad y autorización para la consola.

    En modo fixture (`development_only=True`) autentica a un actor por su
    identificador y rol de aplicación, generando una evidencia con assurance
    DEVELOPMENT y expiración acotada. No es autenticación fuerte: se bloquea si
    el entorno no es de desarrollo.
    """

    # Assurance del modo fixture (nunca tratado como autenticación fuerte).
    FIXTURE_ASSURANCE = "DEVELOPMENT"
    FIXTURE_PROVIDER = "fixture"
    FIXTURE_METHOD = "fixture"

    def __init__(
        self,
        *,
        environment: str,
        development_only: bool = True,
        session_ttl_seconds: int = 3600,
        fixture_roles: dict[str, list[str]] | None = None,
    ):
        self.environment = environment.upper()
        self.development_only = development_only
        self.session_ttl_seconds = session_ttl_seconds
        # Mapa fixture: identifier -> lista de roles de aplicación.
        self._fixture_roles = fixture_roles or {}

    # ── Autenticación ────────────────────────────────────────────────
    def authenticate(
        self, *, identifier: str, role: str | AppRole
    ) -> AuthenticationEvidence:
        """Autentica a un actor en modo fixture y devuelve su evidencia.

        Solo válido en entornos de desarrollo (LOCAL/TEST). En producción se
        bloquea: el fixture no es autenticación fuerte.
        """
        if not self.development_only:
            raise FixtureNotAllowedInProductionError(
                "el modo fixture no es autenticación fuerte; "
                "bloquear configuración en producción"
            )
        if self.environment not in ("LOCAL", "TEST", "DEV", "DEVELOPMENT"):
            raise FixtureNotAllowedInProductionError(
                f"modo fixture no permitido en entorno '{self.environment}'"
            )
        if not identifier:
            raise IdentityError("identifier es obligatorio")
        app_role = AppRole.from_value(role.value if isinstance(role, AppRole) else role)
        now = datetime.now(timezone.utc)
        return AuthenticationEvidence(
            subject=identifier,
            provider=self.FIXTURE_PROVIDER,
            method=self.FIXTURE_METHOD,
            assurance=self.FIXTURE_ASSURANCE,
            identifier=identifier,
            instant=now,
            expires_at=now + timedelta(seconds=self.session_ttl_seconds),
            development_only=True,
        )

    # ── Autorización ─────────────────────────────────────────────────
    def has_role(
        self, evidence: AuthenticationEvidence | None, role: str | AppRole
    ) -> bool:
        """True si la evidencia es vigente y el actor tiene el rol requerido."""
        if evidence is None or evidence.is_expired():
            return False
        app_role = AppRole.from_value(role.value if isinstance(role, AppRole) else role)
        roles = self._fixture_roles.get(evidence.identifier, [])
        return app_role.value in roles

    def require_role(
        self, evidence: AuthenticationEvidence | None, role: str | AppRole
    ) -> AuthenticationEvidence:
        """Exige evidencia vigente + rol; lanza si no se cumple."""
        if evidence is None:
            raise UnauthenticatedError("no hay sesión autenticada")
        if evidence.is_expired():
            raise ExpiredEvidenceError("la evidencia de autenticación venció")
        if not self.has_role(evidence, role):
            raise UnauthorizedRoleError(
                f"el actor '{evidence.identifier}' no tiene el rol "
                f"'{role.value if isinstance(role, AppRole) else role}'"
            )
        return evidence

    def authentication_evidence(
        self, evidence: AuthenticationEvidence | None
    ) -> dict[str, Any] | None:
        """Expone la evidencia serializada (solo campos autorizados)."""
        if evidence is None:
            return None
        return evidence.to_dict()