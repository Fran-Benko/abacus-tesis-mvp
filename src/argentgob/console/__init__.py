"""H8 — Consola sanitizada y resolución autorizada.

Este paquete conecta la cadena de auditoría standalone (H7) con el runtime de
gobernanza (H5/H6) a través de una consola de consulta sanitizada:

- `ConsoleQueryService`: lecturas parametrizadas de `v_governance_console`
  (vista sanitizada, sin raw payload), métricas PASS/BLOCK/HITL, percentiles,
  errores, estados UNKNOWN y approvals PENDING/vencidas.
- `HitlDetailService`: detalle de una approval (IDs, tool/resource, entorno,
  riesgo/códigos, política/versión, preview sanitizado, expiración/edad,
  estado y actor autorizado).
- `ResolutionService`: aprobar/rechazar vía `ApprovalResolver` con identidad
  autorizada + evidencia vigente; distingue APPROVED de SUCCESS/FAILED/UNKNOWN
  y maneja la carrera (dos sesiones, solo una gana) y el hold perdido.

La consola NUNCA expone `execution_arguments` ni el payload crudo (INV-05).
"""
from argentgob.console.service import ConsoleQueryService
from argentgob.console.hitl_detail import HitlDetailService
from argentgob.console.resolution import ResolutionService

__all__ = [
    "ConsoleQueryService",
    "HitlDetailService",
    "ResolutionService",
]