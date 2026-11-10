"""Seed canónico de políticas ABAC desde los YAML declarativos (H5/AV-02).

La fuente canónica es `policies/*.yaml` (`module_c.yaml_policies`); si no hay
archivos, degrada a los perfiles in-memory para no dejar el enforcement sin
fuentes.

AV-02: el seed deja de ser un paso manual del operador y pasa a formar parte del
inicializador (`argentgob.db.init`), que propaga el fallo (exit != 0) y verifica
postcondiciones antes de admitir trabajo.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from argentgob.db.connection import get_connection
from argentgob.module_c.yaml_policies import load_all, to_seed_rows
from argentgob.observability.logger import get_logger

log = get_logger("policy_seeder")

_UPSERT_SQL = """
    INSERT INTO governance_policies (
        policy_id, policy_version, profile_name, tool_name,
        operation_class, environment, resource, effect, priority,
        sensitivity_limit, obligations, transform_spec,
        valid_from, valid_until, policy_digest
    ) VALUES (
        %(policy_id)s, %(policy_version)s, %(profile)s, %(tool)s,
        %(op_class)s, %(environment)s, %(resource)s, %(effect)s,
        %(priority)s, %(sensitivity_limit)s, %(obligations)s,
        %(transform_spec)s, %(valid_from)s, %(valid_until)s,
        %(policy_digest)s
    )
    ON CONFLICT (policy_id, policy_version) DO UPDATE SET
        profile_name = EXCLUDED.profile_name,
        tool_name = EXCLUDED.tool_name,
        operation_class = EXCLUDED.operation_class,
        environment = EXCLUDED.environment,
        resource = EXCLUDED.resource,
        effect = EXCLUDED.effect,
        priority = EXCLUDED.priority,
        sensitivity_limit = EXCLUDED.sensitivity_limit,
        obligations = EXCLUDED.obligations,
        transform_spec = EXCLUDED.transform_spec,
        valid_from = EXCLUDED.valid_from,
        valid_until = EXCLUDED.valid_until,
        policy_digest = EXCLUDED.policy_digest
"""


def seed(
    environment: str | None = None,
    connection_factory: Callable[[], Any] | None = None,
) -> int:
    """Inserta/actualiza las políticas declarativas y poda las no declaradas.

    Retorna el total de filas escritas. Idempotente: usa UPSERT sobre la clave
    compuesta (policy_id, policy_version). El pruning se limita al
    `environment` sembrado, de modo que no borra políticas ajenas (T-A03).
    """
    env = environment or "LOCAL"
    policies = load_all(environment=env)
    if connection_factory is None:
        connection_factory = get_connection
    conn = connection_factory()
    insertadas = 0
    try:
        with conn.cursor() as cur:
            for row in to_seed_rows(policies):
                cur.execute(_UPSERT_SQL, row)
                insertadas += cur.rowcount
                log.info(
                    "policy_seeded",
                    policy_id=row["policy_id"],
                    profile=row["profile"],
                    tool=row["tool"],
                )

            declaradas = {p.policy_id for p in policies}
            cur.execute(
                "SELECT DISTINCT policy_id FROM governance_policies "
                "WHERE environment = %(env)s",
                {"env": env},
            )
            presentes = {
                row.get("policy_id") if isinstance(row, dict) else row[0]
                for row in cur.fetchall()
            }
            for stale in sorted(presentes - declaradas):
                cur.execute(
                    "DELETE FROM governance_policies "
                    "WHERE environment = %(env)s AND policy_id = %(pid)s",
                    {"env": env, "pid": stale},
                )
                log.info("policy_pruned", policy_id=stale)
    finally:
        conn.close()
    return insertadas
