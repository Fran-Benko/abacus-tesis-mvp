"""Carga de políticas ABAC declarativas desde YAML (fuente única).

Hito prioridad media — refinar políticas declarativas:
- Los archivos `policies/*.yaml` son la fuente canónica de políticas: se
  validan estrictamente y se cargan como `GovernancePolicy` (el mismo modelo
  que usa el `PolicyStore` en runtime).
- Vocabulario declarativo de `effect`: `ALLOW` → PASS, `DENY` → BLOCK, y
  `PASS`/`BLOCK`/`HITL` literales. Se normaliza al enum interno.
- Campos opcionales soportados: `priority`, `obligations`,
  `sensitivity_limit`, `resource`, `valid_from`, `valid_until` y
  `transform_spec`.
- Errores de esquema son descriptivos (`PolicyYamlError`) y mencionan el
  archivo, el `policy_id` y el campo responsable.
- Si no existen archivos YAML, `load_all()` degrada a los perfiles
  in-memory (`profiles.PROFILES`) para no dejar el enforcement sin fuentes.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from argentgob.core.errors import GovernanceAction, Obligation
from argentgob.module_c.policy import GovernancePolicy
from argentgob.module_c.profiles import PROFILES

# Vocabulario declarativo de effect -> GovernanceAction interno.
_EFFECT_ALIASES = {
    "ALLOW": "PASS",
    "DENY": "BLOCK",
    "PASS": "PASS",
    "BLOCK": "BLOCK",
    "HITL": "HITL",
}

_REQUIRED_POLICY_FIELDS = (
    "policy_id",
    "tool_name",
    "operation_class",
    "effect",
)

_OBLIGATION_ALIASES = {
    "USE_ORIGINAL_ARGUMENTS": Obligation.USE_ORIGINAL_ARGUMENTS,
    "USE_TRANSFORMED_ARGUMENTS": Obligation.USE_TRANSFORMED_ARGUMENTS,
}


class PolicyYamlError(ValueError):
    """Error de validación de una política declarativa YAML (con contexto)."""


def _policies_dir() -> Path:
    """Directorio raíz `policies/` del repositorio (relativo al paquete)."""
    return Path(__file__).resolve().parents[3] / "policies"


def _normalize_effect(raw: Any) -> str:
    if not isinstance(raw, str):
        raise PolicyYamlError(f"effect debe ser un string, got {type(raw).__name__}")
    effect = _EFFECT_ALIASES.get(raw.upper())
    if effect is None:
        raise PolicyYamlError(
            f"effect inválido '{raw}' (esperado ALLOW|DENY|PASS|BLOCK|HITL)"
        )
    return effect


# Clase de operación por herramienta (mismo mapa que el seed in-memory).
_OPERATION_CLASS = {
    "news": "EXTERNAL_SEND",
    "stock_price": "READ",
    "crypto_price": "READ",
}


def _expand_wildcards(
    policy: GovernancePolicy,
    *,
    profile_name: str,
) -> list[GovernancePolicy]:
    """Expande el comodín `*` de tool/operation_class a las herramientas del
    perfil (matching exacto del evaluador; nunca wildcard libre de runtime).

    El YAML declara `tool_name: "*"` como "todas las herramientas del perfil";
    el loader la expande a una política por tool con `policy_id` único, para
    no colisionar en la clave compuesta (policy_id, policy_version).
    """
    if policy.tool_name != "*" and policy.operation_class != "*":
        return [policy]

    profile = PROFILES.get(profile_name)
    tools = profile.allowed_tools if profile is not None else []
    if not tools:
        raise PolicyYamlError(
            f"política '{policy.policy_id}': comodín '*' sin herramientas "
            f"conocidas para el perfil '{profile_name}'"
        )

    expanded: list[GovernancePolicy] = []
    for tool in tools:
        op_class = (
            _OPERATION_CLASS.get(tool, "READ")
            if policy.operation_class == "*"
            else policy.operation_class
        )
        suffix = tool.upper()
        expanded.append(
            GovernancePolicy(
                policy_id=f"{policy.policy_id}-{suffix}",
                policy_version=policy.policy_version,
                profile_name=profile_name,
                tool_name=tool,
                operation_class=op_class,
                environment=policy.environment,
                resource=policy.resource,
                effect=policy.effect,
                priority=policy.priority,
                sensitivity_limit=policy.sensitivity_limit,
                obligations=policy.obligations,
                transform_spec=policy.transform_spec,
                valid_from=policy.valid_from,
                valid_until=policy.valid_until,
                policy_digest=policy.policy_digest,
            )
        )
    return expanded


def _normalize_obligations(raw: Any) -> list[Obligation]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PolicyYamlError(f"obligations debe ser una lista, got {type(raw).__name__}")
    parsed: list[Obligation] = []
    for item in raw:
        if not isinstance(item, str) or item not in _OBLIGATION_ALIASES:
            raise PolicyYamlError(
                f"obligation inválida '{item}' "
                "(esperado USE_ORIGINAL_ARGUMENTS|USE_TRANSFORMED_ARGUMENTS)"
            )
        parsed.append(_OBLIGATION_ALIASES[item])
    return parsed


def _parse_iso(value: Any, field: str) -> datetime | None:
    """Parsea un timestamp ISO-8601 opcional; exige UTC explícito."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PolicyYamlError(f"{field} debe ser un string ISO-8601")
    try:
        # FURB162: el replace "Z"->"+00:00" es necesario: fromisoformat no
        # acepta el sufijo Z en esta versión de Python.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162
    except ValueError as exc:
        raise PolicyYamlError(f"{field} inválido '{value}': {exc}") from exc
    if parsed.tzinfo is None:
        raise PolicyYamlError(f"{field} debe incluir zona horaria (UTC explícito)")
    return parsed.astimezone(UTC)


def _normalize_priority(raw: Any) -> int:
    if raw is None:
        return 50
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PolicyYamlError(f"priority debe ser entero, got {type(raw).__name__}")
    return raw


def _normalize_transform_spec(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PolicyYamlError(f"transform_spec debe ser un objeto, got {type(raw).__name__}")
    return raw


def policy_from_mapping(
    policy_id: str,
    mapping: dict[str, Any],
    *,
    profile_name: str,
    environment: str,
) -> GovernancePolicy:
    """Convierte un dict de política YAML en `GovernancePolicy` validado."""
    for field in _REQUIRED_POLICY_FIELDS:
        if field not in mapping:
            raise PolicyYamlError(
                f"política '{policy_id}' falta el campo requerido '{field}'"
            )

    obligations = _normalize_obligations(mapping.get("obligations"))

    return GovernancePolicy(
        policy_id=str(mapping["policy_id"]),
        policy_version=int(mapping.get("policy_version", 1)),
        profile_name=profile_name,
        tool_name=str(mapping["tool_name"]),
        operation_class=str(mapping["operation_class"]),
        environment=mapping.get("environment", environment),
        resource=mapping.get("resource"),
        effect=GovernanceAction(_normalize_effect(mapping["effect"])),
        priority=_normalize_priority(mapping.get("priority")),
        sensitivity_limit=mapping.get("sensitivity_limit"),
        obligations=obligations,
        transform_spec=_normalize_transform_spec(mapping.get("transform_spec")),
        valid_from=_parse_iso(mapping.get("valid_from"), "valid_from"),
        valid_until=_parse_iso(mapping.get("valid_until"), "valid_until"),
        policy_digest=mapping.get("policy_digest", ""),
    )


def parse_policy_file(path: Path, *, environment: str) -> list[GovernancePolicy]:
    """Parsea un archivo YAML de políticas y valida su esquema.

    Estructura esperada:
        profile: analyst
        policies:
          - policy_id: "POL-analyst-001"
            tool_name: "news"
            operation_class: "EXTERNAL_SEND"
            effect: "ALLOW"
            priority: 50
            obligations: ["USE_ORIGINAL_ARGUMENTS"]
            sensitivity_limit: "MEDIUM"
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyYamlError(f"no se pudo leer {path.name}: {exc}") from exc

    if not isinstance(raw, dict):
        raise PolicyYamlError(f"{path.name}: el documento debe ser un objeto")

    profile_name = raw.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise PolicyYamlError(f"{path.name}: falta 'profile' (string requerido)")

    policies_raw = raw.get("policies")
    if not isinstance(policies_raw, list) or not policies_raw:
        raise PolicyYamlError(f"{path.name}: falta 'policies' (lista no vacía requerida)")

    parsed: list[GovernancePolicy] = []
    for item in policies_raw:
        if not isinstance(item, dict) or "policy_id" not in item:
            raise PolicyYamlError(
                f"{path.name}: cada política debe ser un objeto con 'policy_id'"
            )
        policy = policy_from_mapping(
            str(item["policy_id"]),
            item,
            profile_name=profile_name,
            environment=environment,
        )
        for expanded in _expand_wildcards(policy, profile_name=profile_name):
            if any(p.policy_id == expanded.policy_id for p in parsed):
                raise PolicyYamlError(
                    f"{path.name}: policy_id duplicado '{expanded.policy_id}'"
                )
            parsed.append(expanded)
    return parsed


def policies_from_profiles(environment: str = "LOCAL") -> list[GovernancePolicy]:
    """Fallback: genera una política ALLOW por herramienta permitida por perfil."""
    policies: list[GovernancePolicy] = []
    for profile in PROFILES.values():
        for tool in profile.allowed_tools:
            policies.append(
                GovernancePolicy(
                    policy_id=f"POL-{profile.name}-{tool}",
                    policy_version=1,
                    profile_name=profile.name,
                    tool_name=tool,
                    operation_class=_OPERATION_CLASS.get(tool, "READ"),
                    environment=environment,
                    effect=GovernanceAction.PASS,
                    priority=50,
                    obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
                )
            )
    return policies


def load_all(directory: Path | None = None, *, environment: str = "LOCAL") -> list[GovernancePolicy]:
    """Carga todas las políticas declarativas de `policies/*.yaml`.

    Degrada a los perfiles in-memory si no hay archivos YAML, para que el
    enforcement nunca quede sin fuentes (fail-closed solo para escrituras).
    """
    directory = directory or _policies_dir()
    files = sorted(directory.glob("*.yaml")) if directory.exists() else []
    if not files:
        return policies_from_profiles(environment=environment)

    policies: list[GovernancePolicy] = []
    for path in files:
        policies.extend(parse_policy_file(path, environment=environment))
    return policies


def to_seed_rows(
    policies: list[GovernancePolicy],
) -> list[dict[str, Any]]:
    """Serializa políticas al formato de INSERT de `seed_policies.seed()`."""
    rows: list[dict[str, Any]] = []
    for p in policies:
        rows.append(
            {
                "policy_id": p.policy_id,
                "policy_version": p.policy_version,
                "profile": p.profile_name,
                "tool": p.tool_name,
                "op_class": p.operation_class,
                "environment": p.environment,
                "resource": p.resource,
                "effect": p.effect.value,
                "priority": p.priority,
                "sensitivity_limit": p.sensitivity_limit,
                "obligations": (
                    json.dumps([o.value for o in p.obligations]) if p.obligations else None
                ),
                "transform_spec": (
                    json.dumps(p.transform_spec) if p.transform_spec else None
                ),
                "valid_from": p.valid_from or datetime.now(UTC),
                "valid_until": p.valid_until,
                "policy_digest": p.policy_digest or None,
            }
        )
    return rows
