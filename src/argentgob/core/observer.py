"""Contrato mínimo de observación de fronteras de gobernanza (AV-01).

El PEP y las tools gobernadas notifican hechos verificables (tentativa,
decisión, inicio/fin de ejecución) a un observador inyectado. El observador
**no decide ni ejecuta**: solo proyecta eventos acotados. Si falla, se registra
la degradación y la decisión de negocio no cambia.

El contrato usa strings (no enums del paquete `live`) para no acoplar el
middleware a la capa de servicio HTTP.
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GovernanceObserver(Protocol):
    """Receptor de eventos de frontera de gobernanza."""

    def on_governance_event(self, kind: str, payload: dict[str, Any]) -> None:
        """Recibe un evento ya acotado por allowlist."""
        ...


class NullObserver:
    """Observador no-op: conserva el comportamiento de CLI y tests."""

    def on_governance_event(self, kind: str, payload: dict[str, Any]) -> None:
        return None
