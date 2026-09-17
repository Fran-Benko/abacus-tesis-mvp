"""
Transformación de argumentos para R2 — Frontera gobernada.

La selección de argumentos exige EXACTAMENTE una obligación:
- USE_ORIGINAL_ARGUMENTS: la herramienta recibe los argumentos originales intactos.
- USE_TRANSFORMED_ARGUMENTS: la herramienta recibe el objeto transformado y
  validado, nunca una vista previa.

Ninguna o ambas obligaciones producen POLICY_CONFLICT y cero ejecución.
"""
from typing import Any

from argentgob.core.errors import Obligation, ReasonCode


class ArgumentTransformError(Exception):
    """Error al transformar o seleccionar argumentos (R2)."""

    def __init__(self, reason: ReasonCode, message: str):
        self.reason = reason
        super().__init__(message)


def select_effective_arguments(
    execution_arguments: dict[str, Any],
    obligations: list[Obligation],
) -> dict[str, Any]:
    """Selecciona los argumentos efectivos según las obligaciones de la decisión.

    Exige exactamente una obligación de selección. Ninguna o ambas lanzan
    ArgumentTransformError con POLICY_CONFLICT (cero ejecución).
    """
    selection = [
        o
        for o in obligations
        if o in (Obligation.USE_ORIGINAL_ARGUMENTS, Obligation.USE_TRANSFORMED_ARGUMENTS)
    ]
    if len(selection) != 1:
        raise ArgumentTransformError(
            ReasonCode.POLICY_CONFLICT,
            "se requiere exactamente una obligación de selección de argumentos",
        )

    if selection[0] == Obligation.USE_ORIGINAL_ARGUMENTS:
        return dict(execution_arguments)

    return transform_arguments(execution_arguments)


def transform_arguments(execution_arguments: dict[str, Any]) -> dict[str, Any]:
    """Aplica la transformación mínima (normalización) a los argumentos.

    ADR-0001: normalización mínima — solo strip de espacios en valores string.
    Si la transformación produce un objeto inválido, lanza ARGUMENT_TRANSFORM_FAILED.
    """
    transformed: dict[str, Any] = {}
    for key, value in execution_arguments.items():
        if isinstance(value, str):
            transformed[key] = value.strip()
        else:
            transformed[key] = value

    if not isinstance(transformed, dict):
        raise ArgumentTransformError(
            ReasonCode.ARGUMENT_TRANSFORM_FAILED,
            "la transformación no produjo un objeto válido",
        )
    return transformed