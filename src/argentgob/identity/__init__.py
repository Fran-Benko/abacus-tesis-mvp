"""H8 — Identidad y autorización de la consola.

Roles de aplicación separados (viewer/auditor/policy_admin/operator) y un
`AuditorIdentityProvider` con `authenticate`, `has_role` y
`authentication_evidence`. El modo fixture está marcado `development_only=true`
con assurance DEVELOPMENT y advertencia visible; no se inventa integración
productiva/MFA.
"""
from argentgob.identity.evidence import AuthenticationEvidence
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole

__all__ = [
    "AppRole",
    "AuthenticationEvidence",
    "AuditorIdentityProvider",
]