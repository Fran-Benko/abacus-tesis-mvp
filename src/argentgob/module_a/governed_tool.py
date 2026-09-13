"""
GovernedTool: wrapper base que aplica gobernanza sobre cualquier herramienta CrewAI.
Implementa INV-01 (ningún executor antes de decision_id) e INV-02 (BLOCK no ejecuta).

CrewAI usa Pydantic v2. Los campos de gobernanza (governance, agent_identity)
no son tipos primitivos, por eso se declaran Optional con default None y se
habilita arbitrary_types_allowed en la configuración del modelo.
"""
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import ConfigDict

from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import HookAborted
from argentgob.module_a.governance import GovernanceMiddleware


class GovernedTool(BaseTool):
    """Clase base gobernada. Las herramientas concretas heredan de esta."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Metadatos de gobernanza (declarados/ajustados por cada subclase).
    operation_class: str = "READ"
    resource: str = "unspecified"
    governance: Optional[GovernanceMiddleware] = None
    agent_identity: Optional[AgentIdentity] = None

    def _run(self, **kwargs: Any) -> str:
        """Flujo de ejecución gobernada.

        1. pre_hook() -> puede lanzar HookAborted (BLOCK); la herramienta no corre.
        2. _execute() si PASS.
        3. post_hook() -> registra el resultado.
        """
        if self.governance and self.agent_identity:
            envelope = self.governance.pre_hook(
                agent=self.agent_identity,
                tool_name=self.name,
                operation_class=self.operation_class,
                resource=self.resource,
                execution_arguments=kwargs,
            )
            try:
                result = self._execute(**kwargs)
                self.governance.post_hook(
                    envelope=envelope, result=result, error=None
                )
                return result
            except HookAborted:
                # Propagar sin registrar como error de ejecución de la herramienta.
                raise
            except Exception as e:  # noqa: BLE001 - se registra y se re-lanza
                self.governance.post_hook(envelope=envelope, result=None, error=e)
                raise
        else:
            # Sin gobernanza configurada: ejecución directa (solo tests unitarios).
            return self._execute(**kwargs)

    def _execute(self, **kwargs: Any) -> str:
        """Implementar en cada herramienta concreta."""
        raise NotImplementedError
