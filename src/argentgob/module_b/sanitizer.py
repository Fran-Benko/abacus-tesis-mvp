"""
Sanitizador básico: reemplaza valores largos por descriptores de tamaño
y enmascara patrones de datos sensibles.

INV-04: telemetry_arguments no se reutilizan como argumentos operativos.
INV-05: el payload crudo no aparece en logs ni en la base de datos.
INV-16: el detector produce metadata/finding sin valor y no muta execution_arguments.
"""
import re
from typing import Any

from argentgob.core.config import Settings

# Patrones de datos sensibles básicos (MVP; detectores avanzados en MVP+1).
# El orden importa: los patrones más específicos deben ir primero.
_PATTERNS = [
    (re.compile(r"(?i)(password|passwd|secret|token|api_key)\s*[:=]\s*\S+"), "SECRET"),
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z.]+"), "EMAIL"),
    (re.compile(r"\b[0-9]{16,19}\b"), "PAYMENT_NUMBER"),  # PAN básico
    (re.compile(r"\b[0-9]{7,8}\b"), "POSSIBLE_DNI"),  # DNI argentino
]


class Sanitizer:
    """Produce telemetry_arguments desde execution_arguments sin exponer datos crudos."""

    def __init__(self, settings: Settings):
        self.max_str_bytes = settings.max_string_bytes

    def sanitize(self, args: dict[str, Any]) -> dict[str, Any]:
        """Produce una copia sanitizada. NO modifica el objeto original (INV-04)."""
        return {k: self._sanitize_value(v) for k, v in args.items()}

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitize_string(value)
        if isinstance(value, dict):
            return {k: self._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(v) for v in value]
        return value

    def _sanitize_string(self, s: str) -> str:
        # Truncar strings demasiado largos (antes de escanear patrones).
        encoded = s.encode("utf-8")
        if len(encoded) > self.max_str_bytes:
            return f"[TRUNCATED:{len(encoded)}bytes]"

        # Enmascarar patrones sensibles.
        for pattern, label in _PATTERNS:
            if pattern.search(s):
                return f"[REDACTED:{label}:{len(s)}chars]"

        # Si es corto y no contiene patrones sensibles, devolver tal cual.
        return s
