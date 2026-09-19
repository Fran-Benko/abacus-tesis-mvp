"""H6 — Aprobacion humana durable y ejecucion gobernada (HITL en el loop).

Este paquete implementa la frontera de aprobacion humana del plan:
hold de argumentos en memoria (idempotente y acotado), creacion/idempotencia
de approvals PENDING, notificacion y resolucion por auditor autorizado,
resume con ExecutionCapability de un solo uso y ejecucion gobernada con
estados SUCCESS/FAILED/UNKNOWN y recuperacion sin retry automatico.

La interfaz publica de reanudacion es una unica funcion:
`GovernedResumeService.resume(token_id, presented_token)`.
"""
from argentgob.hitl.argument_hold import InMemoryArgumentHold
from argentgob.hitl.governed_resume import GovernedResumeService

__all__ = [
    "InMemoryArgumentHold",
    "GovernedResumeService",
]