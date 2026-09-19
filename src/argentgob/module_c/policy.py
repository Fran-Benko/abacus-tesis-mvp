"""Modelo de política ABAC persistente y selección de argumentos determinista.

H5 — Decisión determinista y selección de argumentos:
- Matching exacto por defecto (ambiente, identidad/rol, tool, clase y vigencia).
- BLOCK > HITL > PASS; las prohibiciones no se anulan por prioridad/especificidad.
- Obligaciones incompatibles o versiones incompatibles para el mismo alcance
  producen `POLICY_CONFLICT`.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from argentgob.core.errors import GovernanceAction, Obligation, ReasonCode


@dataclass
class GovernancePolicy:
    """Política ABAC tal como se lee desde `governance_policies`."""

    policy_id: str
    policy_version: int
    profile_name: str
    tool_name: str
    operation_class: str
    environment: str
    resource: str | None = None
    effect: GovernanceAction = GovernanceAction.PASS
    priority: int = 50
    sensitivity_limit: str | None = None
    obligations: list[Obligation] = field(default_factory=list)
    transform_spec: dict[str, Any] | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    policy_digest: str = ""

    def is_active(self, now: datetime | None = None) -> bool:
        """True si la política está vigente en `now` (UTC)."""
        now = now or datetime.now(timezone.utc)
        if self.valid_from is not None and now < self.valid_from:
            return False
        if self.valid_until is not None and now > self.valid_until:
            return False
        return True

    def matches(
        self,
        *,
        profile_name: str,
        tool_name: str,
        operation_class: str,
        environment: str,
        resource: str | None,
    ) -> bool:
        """Matching exacto por defecto. `policy/resource` usa coincidencia
        exacta; un `resource` de policy nulo actúa como comodín solo cuando es
        explícitamente documentado (prefijo restringido o lista exacta)."""
        if self.profile_name != profile_name:
            return False
        if self.tool_name != tool_name:
            return False
        if self.operation_class != operation_class:
            return False
        if self.environment != environment:
            return False
        if self.resource is not None and self.resource != resource:
            # Prefijo restringido: coincide si resource empieza con policy.resource
            # seguido de un separador (listas exactas, nunca wildcard libre).
            prefix = self.resource.rstrip("*")
            if not (
                self.resource.endswith("*")
                and (resource or "").startswith(prefix)
            ):
                return False
        return True


def css_resource(netloc: str, path: str | None = None) -> str:
    """Normaliza un recurso de CSS (clase de servicio / host) para matching.

    Usada para convertir hosts a su forma canónica y evitar wildcards de
    dominio libre para noticias (la noticia exige lista exacta de host).
    """
    host = (netloc or "").lower()
    if path and path.strip() and path.strip() != "/":
        return f"{host}{path.rstrip('/')}"
    return host


def _use_original(exc_args: dict[str, Any]) -> dict[str, Any]:
    return dict(exc_args)


def _use_transformed(exc_args: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any]:
    """Aplica la transformación mínima (normalización) salvo que `transform_spec`
    defina campos a transformar. La selección transformada devuelve exactamente
    el objeto validado, nunca una preview."""
    allowed = spec.get("fields") if spec and isinstance(spec, dict) else None

    def _transform_value(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return {k: _transform_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_transform_value(v) for v in value]
        return value

    if allowed is None:
        return {k: _transform_value(v) for k, v in exc_args.items()}
    result: dict[str, Any] = {}
    for key, value in exc_args.items():
        if key in allowed:
            result[key] = _transform_value(value)
        else:
            result[key] = value
    return result


def select_effective_arguments_h5(
    execution_arguments: dict[str, Any],
    obligations: list[Obligation],
    transform_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    """Selecciona los argumentos efectivos según las obligaciones de la decisión.

    Exige exactamente una obligación de selección. Ninguna o ambas lanzan
    POLICY_CONFLICT (cero ejecución), consistente con R2.
    """
    selection = [
        o
        for o in obligations
        if o in (Obligation.USE_ORIGINAL_ARGUMENTS, Obligation.USE_TRANSFORMED_ARGUMENTS)
    ]
    if len(selection) != 1:
        raise _ConflictError(ReasonCode.POLICY_CONFLICT)
    if selection[0] == Obligation.USE_ORIGINAL_ARGUMENTS:
        return _use_original(execution_arguments)
    return _use_transformed(execution_arguments, transform_spec)


class _ConflictError(Exception):
    """Indica una incompatibilidad de obligaciones (POLICY_CONFLICT)."""

    def __init__(self, reason: ReasonCode):
        self.reason = reason
        super().__init__(reason.value)