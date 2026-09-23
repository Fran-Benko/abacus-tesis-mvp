"""Roles de aplicación de la consola H8.

Son roles de aplicación, NO credenciales de base de datos. Separan qué puede
hacer cada actor en la consola:

- `viewer`: consulta la vista sanitizada y el detalle HITL (solo lectura).
- `auditor`: resuelve approvals (aprobar/rechazar) con evidencia vigente.
- `policy_admin`: versiona políticas por servicio (fuera del alcance de la UI).
- `operator`: solo recuperación no destructiva (fuera del alcance de la UI).

Resolver exige rol `auditor` + evidencia vigente en el backend, no solo ocultar
el botón en la UI.
"""
from enum import Enum


class AppRole(str, Enum):
    """Roles de aplicación de la consola (no confundir con roles DB)."""

    VIEWER = "viewer"
    AUDITOR = "auditor"
    POLICY_ADMIN = "policy_admin"
    OPERATOR = "operator"

    @classmethod
    def from_value(cls, value: str) -> "AppRole":
        """Resuelve un rol por su valor; lanza ValueError si es desconocido."""
        return cls(value)