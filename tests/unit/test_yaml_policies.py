"""Tests del loader de políticas declarativas YAML (hito prioridad media).

Cubre:
- Parseo de un archivo YAML válido con todos los campos declarativos.
- Expansión del comodín `*` (tool/operation_class) a las herramientas del perfil.
- Normalización de `effect` (ALLOW→PASS, DENY→BLOCK) y de obligations.
- Errores descriptivos: campos requeridos faltantes, effect inválido,
  obligations inválidas, duplicados, timestamps sin zona horaria.
- Degradación a perfiles in-memory cuando no hay archivos YAML.
- Serialización a filas de seed (to_seed_rows) con el contrato del INSERT.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argentgob.core.errors import GovernanceAction, Obligation
from argentgob.module_c.yaml_policies import (
    PolicyYamlError,
    load_all,
    parse_policy_file,
    policy_from_mapping,
    to_seed_rows,
)


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "policies.yaml"
    p.write_text(content, encoding="utf-8")
    return p


_VALID = """\
profile: analyst
policies:
  - policy_id: "POL-analyst-001"
    tool_name: "news"
    operation_class: "EXTERNAL_SEND"
    effect: "ALLOW"
    priority: 50
    sensitivity_limit: "MEDIUM"
    obligations: ["USE_ORIGINAL_ARGUMENTS"]
"""


def test_parse_yaml_valido_con_campos_declarativos(tmp_path):
    path = _write(tmp_path, _VALID)
    policies = parse_policy_file(path, environment="LOCAL")
    assert len(policies) == 1
    p = policies[0]
    assert p.policy_id == "POL-analyst-001"
    assert p.profile_name == "analyst"
    assert p.tool_name == "news"
    assert p.operation_class == "EXTERNAL_SEND"
    assert p.effect == GovernanceAction.PASS  # ALLOW -> PASS
    assert p.priority == 50
    assert p.sensitivity_limit == "MEDIUM"
    assert p.obligations == [Obligation.USE_ORIGINAL_ARGUMENTS]


def test_effect_deny_se_normaliza_a_block(tmp_path):
    content = _VALID.replace("effect: \"ALLOW\"", "effect: \"DENY\"")
    policies = parse_policy_file(_write(tmp_path, content), environment="LOCAL")
    assert policies[0].effect == GovernanceAction.BLOCK


def test_load_all_expande_comodin_a_herramientas_del_perfil(tmp_path):
    (tmp_path / "admin.yaml").write_text(
        """\
profile: admin
policies:
  - policy_id: "POL-admin-001"
    tool_name: "*"
    operation_class: "*"
    effect: "ALLOW"
    priority: 10
    sensitivity_limit: "HIGH"
    obligations: ["USE_ORIGINAL_ARGUMENTS"]
""",
        encoding="utf-8",
    )
    policies = load_all(directory=tmp_path, environment="LOCAL")
    assert len(policies) == 3  # news, stock_price, crypto_price
    tools = {p.tool_name for p in policies}
    assert tools == {"news", "stock_price", "crypto_price"}
    assert all(p.policy_id.startswith("POL-admin-001-") for p in policies)
    assert all(p.obligations == [Obligation.USE_ORIGINAL_ARGUMENTS] for p in policies)


def test_load_all_sin_yaml_degrada_a_perfiles(tmp_path, monkeypatch):
    # Directorio vacío: sin archivos YAML -> fallback a PROFILES.
    policies = load_all(directory=tmp_path, environment="LOCAL")
    assert len(policies) >= 5  # analyst 3 + analyst_restricted 2
    assert all(p.effect == GovernanceAction.PASS for p in policies)


def test_policy_id_duplicado_lanza_error(tmp_path):
    content = _VALID + '  - policy_id: "POL-analyst-001"\n    tool_name: "stock_price"\n    operation_class: "READ"\n    effect: "ALLOW"\n'
    with pytest.raises(PolicyYamlError, match="duplicado"):
        parse_policy_file(_write(tmp_path, content), environment="LOCAL")


def test_campo_requerido_faltante_lanza_error(tmp_path):
    broken = """\
profile: analyst
policies:
  - policy_id: "POL-x"
    tool_name: "news"
    effect: "ALLOW"
"""
    with pytest.raises(PolicyYamlError, match="operation_class"):
        parse_policy_file(_write(tmp_path, broken), environment="LOCAL")


def test_effect_invalido_lanza_error(tmp_path):
    broken = _VALID.replace("ALLOW", "GRANT")
    with pytest.raises(PolicyYamlError, match="effect inválido"):
        parse_policy_file(_write(tmp_path, broken), environment="LOCAL")


def test_obligation_invalida_lanza_error(tmp_path):
    broken = _VALID.replace("USE_ORIGINAL_ARGUMENTS", "USE_BOTH")
    with pytest.raises(PolicyYamlError, match="obligation inválida"):
        parse_policy_file(_write(tmp_path, broken), environment="LOCAL")


def test_valid_from_sin_zona_lanza_error(tmp_path):
    broken = _VALID.replace(
        "obligations: [\"USE_ORIGINAL_ARGUMENTS\"]",
        'obligations: ["USE_ORIGINAL_ARGUMENTS"]\n    valid_from: "2026-01-01T00:00:00"',
    )
    with pytest.raises(PolicyYamlError, match="zona horaria"):
        parse_policy_file(_write(tmp_path, broken), environment="LOCAL")


def test_valid_from_con_utc_se_parsea(tmp_path):
    good = _VALID.replace(
        "obligations: [\"USE_ORIGINAL_ARGUMENTS\"]",
        'obligations: ["USE_ORIGINAL_ARGUMENTS"]\n    valid_from: "2026-01-01T00:00:00Z"',
    )
    policies = parse_policy_file(_write(tmp_path, good), environment="LOCAL")
    assert policies[0].valid_from is not None


def test_to_seed_rows_serializa_contrato_del_insert():
    policy = policy_from_mapping(
        "POL-x",
        {
            "policy_id": "POL-x",
            "tool_name": "news",
            "operation_class": "EXTERNAL_SEND",
            "effect": "ALLOW",
            "obligations": ["USE_ORIGINAL_ARGUMENTS"],
            "sensitivity_limit": "MEDIUM",
        },
        profile_name="analyst",
        environment="LOCAL",
    )
    rows = to_seed_rows([policy])
    assert len(rows) == 1
    row = rows[0]
    assert row["policy_id"] == "POL-x"
    assert row["effect"] == "PASS"
    assert row["obligations"] == '["USE_ORIGINAL_ARGUMENTS"]'
    assert row["sensitivity_limit"] == "MEDIUM"
    assert row["resource"] is None
