"""
SpyTool: herramienta de prueba que cuenta cuántas veces fue ejecutada.
Sirve para verificar INV-01 (no se ejecuta antes de una decisión) e
INV-02 (BLOCK no produce ejecución de la herramienta).
"""
from typing import Any

from argentgob.module_a.governed_tool import GovernedTool


class SpyTool(GovernedTool):
    """Herramienta espía para tests de enforcement."""

    name: str = "spy_tool"
    description: str = "Herramienta espía para tests de enforcement."
    operation_class: str = "READ"
    resource: str = "test"
    call_count: int = 0

    def _execute(self, **kwargs: Any) -> str:
        self.call_count += 1
        return f"spy_executed:{self.call_count}"
