"""
FeatureBuilder: deriva las features pre-ejecución del `ToolCallEnvelope`.

Fase 4 — Integración operacional. Replica el contrato de features de
`feature_schema_v1` (docs/analytics/risk_dataset_schema.json) usado por el
entrenador (Fase 3), para que la inferencia en el `pre_hook` use exactamente
las mismas columnas y el mismo orden que el modelo entrenado.

Reglas (spec §2, INV-04/05):
- Las features se calculan SIEMPRE sobre `telemetry_arguments` (versión
  sanitizada), nunca sobre `execution_arguments` crudos.
- Solo se usan datos disponibles en el `pre_hook` (sin resultado de
  herramienta, sin `observed_action`, sin `post_hook`).
- Las features de control describen el estado de gobierno (políticas
  coincidentes, vigencia, sensibilidad) sin acoplar la etiqueta.

El FeatureBuilder es inyectable: recibe un `policy_source` (PolicyStore) para
derivar `control.*` de las políticas reales. Si no hay policy_source o la DB
no está disponible, las features de control se degradan a valores neutros
(0/false).
"""
from __future__ import annotations

import re
from typing import Any

from argentgob.core.envelope import ToolCallEnvelope
from argentgob.module_c.profiles import PROFILES

# Orden canónico de features (debe coincidir con el entrenador Fase 3).
FEATURE_ORDER: list[str] = [
    # context
    "context.tool_name",
    "context.operation_class",
    "context.agent_role",
    "context.profile",
    "context.environment",
    "context.resource",
    "context.hour_bucket",
    # payload
    "payload.payload_size_bytes",
    "payload.arg_count",
    "payload.json_depth",
    "payload.sanitized_text_length",
    "payload.has_url",
    "payload.has_sql_injection_pattern",
    "payload.has_shell_pattern",
    "payload.pii_category_count",
    "payload.has_pii",
    "payload.dominant_data_type",
    # control
    "control.tool_allowed_for_profile",
    "control.matched_policy_count",
    "control.has_policy_conflict",
    "control.max_allowed_sensitivity",
    "control.policy_is_active",
    "control.guardrail_query_injection",
    "control.guardrail_topic_relevance",
    "control.guardrail_rate_limit",
    "control.rate_limit_consumption",
    "control.operation_type_requested",
]

# Patrones de inyección (espejo de module_c/guardrails.py).
_INJECTION_PATTERNS = [
    re.compile(r"(?i)(drop\s+table|delete\s+from|insert\s+into|exec\s*\()"),
    re.compile(r"[;'\"]{2,}"),
    re.compile(r"(?i)(ignore\s+previous|forget\s+instructions|you\s+are\s+now)"),
]

# Patrones shell (espejo del corpus).
_SHELL_PATTERNS = re.compile(r"rm\s+-rf|;|&&|\|\|")

# Patrones de PII (espejo de module_b/sanitizer.py).
_PII_PATTERNS = [
    (re.compile(r"(?i)(password|passwd|secret|token|api_key)\s*[:=]\s*\S+"), "SECRET"),
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z.]+"), "EMAIL"),
    (re.compile(r"\b[0-9]{16,19}\b"), "PAYMENT_NUMBER"),
    (re.compile(r"\b[0-9]{7,8}\b"), "POSSIBLE_DNI"),
]

# Sensibilidad máxima permitida por las políticas coincidentes.
#
# El corpus de entrenamiento (`scripts/build_synthetic_corpus.py`) codifica
# `control.max_allowed_sensitivity` como indicador ordinal 0/1: 1 cuando existe
# al menos una política coincidente y 0 cuando no hay ninguna. La inferencia
# debe reproducir esa misma codificación; usar la severidad del
# `operation_class` (READ=1, EXTERNAL_SEND=2, ...) desplazaba la feature fuera
# del rango entrenado y producía train/serve skew: `news` (EXTERNAL_SEND)
# recibía 2 y el modelo lo clasificaba MEDIUM en casi todos los casos.
_SENSITIVITY_WITH_POLICY = 1
_SENSITIVITY_WITHOUT_POLICY = 0


def _flatten(obj: Any) -> list[Any]:
    """Aplana recursivamente dicts/listas a una lista de valores escalares."""
    flat: list[Any] = []
    if isinstance(obj, dict):
        for v in obj.values():
            flat.extend(_flatten(v))
    elif isinstance(obj, list):
        for v in obj:
            flat.extend(_flatten(v))
    else:
        flat.append(obj)
    return flat


def _json_depth(obj: Any) -> int:
    """Profundidad máxima de la estructura JSON (1 = escalar).

    Debe coincidir con `scripts/build_synthetic_corpus.py::json_depth`, que es
    el que generó el corpus de entrenamiento. Un escalar vale 1 y un dict
    `{"query": "AAPL", "max_results": 3}` vale 2. Contar el escalar como 0
    desplazaba la feature un nivel por debajo del rango entrenado y sesgaba la
    inferencia (train/serve skew).
    """
    if isinstance(obj, dict):
        return 1 + max((_json_depth(v) for v in obj.values()), default=0)
    if isinstance(obj, list):
        return 1 + max((_json_depth(v) for v in obj), default=0)
    return 1


def _dominant_type(values: list[Any]) -> str:
    """Tipo de dato dominante entre los valores (string/number/boolean/...)."""
    if not values:
        return "null"
    counts: dict[str, int] = {}
    for v in values:
        if isinstance(v, bool):
            t = "boolean"
        elif isinstance(v, (int, float)):
            t = "number"
        elif isinstance(v, str):
            t = "string"
        elif isinstance(v, dict):
            t = "object"
        elif isinstance(v, list):
            t = "array"
        else:
            t = "null"
        counts[t] = counts.get(t, 0) + 1
    return max(counts, key=counts.get)


class FeatureBuilder:
    """Construye el vector de features pre-ejecución de un envelope."""

    def __init__(self, policy_source: Any = None, rate_limit_consumption: int = 0):
        self.policy_source = policy_source
        self.rate_limit_consumption = rate_limit_consumption

    def build(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        """Retorna el dict de features aplanadas (claves `grupo.nombre`)."""
        telemetry = envelope.telemetry_arguments or {}
        flat = _flatten(telemetry)
        query_text = " ".join(str(v) for v in flat if isinstance(v, str))

        # ── context ────────────────────────────────────────────────
        profile = envelope.agent.role if envelope.agent else ""
        context = {
            "tool_name": envelope.tool_name,
            "operation_class": envelope.operation_class,
            "agent_role": profile,
            "profile": profile,
            "environment": envelope.environment,
            "resource": envelope.resource,
            "hour_bucket": envelope.received_at.hour,
        }

        # ── payload (sobre telemetría sanitizada) ──────────────────
        has_url = bool(re.search(r"https?://|www\.", query_text))
        has_sql = any(p.search(query_text) for p in _INJECTION_PATTERNS)
        has_shell = bool(_SHELL_PATTERNS.search(query_text))
        pii_categories: list[str] = []
        for pattern, label in _PII_PATTERNS:
            if pattern.search(query_text):
                pii_categories.append(label)
        pii_categories = list(dict.fromkeys(pii_categories))

        payload = {
            "payload_size_bytes": envelope.payload_size_bytes,
            "arg_count": len(flat),
            "json_depth": _json_depth(telemetry),
            "sanitized_text_length": len(query_text),
            "has_url": has_url,
            "has_sql_injection_pattern": has_sql,
            "has_shell_pattern": has_shell,
            "pii_category_count": len(pii_categories),
            "has_pii": len(pii_categories) > 0,
            "dominant_data_type": _dominant_type(flat),
        }

        # ── control (estado de gobierno, sin acoplar la etiqueta) ──
        control = self._build_control(envelope, profile)

        return {
            **{f"context.{k}": v for k, v in context.items()},
            **{f"payload.{k}": v for k, v in payload.items()},
            **{f"control.{k}": v for k, v in control.items()},
        }

    def _build_control(self, envelope: ToolCallEnvelope, profile: str) -> dict[str, Any]:
        """Deriva las features de control desde el policy_source (si existe)."""
        tool_allowed = (
            profile in PROFILES
            and envelope.tool_name in PROFILES[profile].allowed_tools
        )
        matched = 0
        has_conflict = False
        max_sensitivity = 0
        policy_active = False

        if self.policy_source is not None:
            try:
                policies = self.policy_source.load_current()
                matches = [
                    p
                    for p in policies
                    if p.matches(
                        profile_name=profile,
                        tool_name=envelope.tool_name,
                        operation_class=envelope.operation_class,
                        environment=envelope.environment,
                        resource=envelope.resource,
                    )
                ]
                matched = len(matches)
                if matches:
                    policy_active = any(p.is_active() for p in matches)
                    # Misma codificación ordinal 0/1 que el corpus entrenado.
                    max_sensitivity = _SENSITIVITY_WITH_POLICY
                    # Conflicto: obligaciones incompatibles entre políticas.
                    seen: dict[str, set[str]] = {}
                    for p in matches:
                        for ob in p.obligations:
                            seen.setdefault(ob.value, set()).add(p.policy_id)
                    has_conflict = (
                        "USE_ORIGINAL_ARGUMENTS" in seen
                        and "USE_TRANSFORMED_ARGUMENTS" in seen
                    )
            except Exception:  # noqa: BLE001 - degradación segura de control
                matched = 0
                has_conflict = False
                max_sensitivity = 0
                policy_active = False

        # Guardrails: espejo de los detectores del dominio.
        query_text = " ".join(
            str(v)
            for v in _flatten(envelope.telemetry_arguments or {})
            if isinstance(v, str)
        )
        guardrail_query_injection = any(p.search(query_text) for p in _INJECTION_PATTERNS)
        guardrail_topic_relevance = (
            envelope.tool_name == "news"
            and bool(
                re.search(
                    r"(?i)(bitcoin|ethereum|btc|eth|stock|accion|cripto|precio|price|market|nasdaq|nyse|sp500)",
                    query_text,
                )
            )
        )
        guardrail_rate_limit = self.rate_limit_consumption < 5

        return {
            "tool_allowed_for_profile": tool_allowed,
            "matched_policy_count": matched,
            "has_policy_conflict": has_conflict,
            "max_allowed_sensitivity": max_sensitivity,
            "policy_is_active": policy_active,
            "guardrail_query_injection": guardrail_query_injection,
            "guardrail_topic_relevance": guardrail_topic_relevance,
            "guardrail_rate_limit": guardrail_rate_limit,
            "rate_limit_consumption": self.rate_limit_consumption,
            "operation_type_requested": (
                "query" if envelope.tool_name == "news" else "get_price"
            ),
        }

    def to_dataframe(self, envelope: ToolCallEnvelope):
        """Retorna un DataFrame de una fila con las features en orden canónico."""
        import pandas as pd

        feats = self.build(envelope)
        row = {k: feats.get(k) for k in FEATURE_ORDER}
        return pd.DataFrame([row], columns=FEATURE_ORDER)
