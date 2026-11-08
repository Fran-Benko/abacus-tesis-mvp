"""
scripts/seed_policies.py

Carga las pol?ticas ABAC en PostgreSQL usando los archivos declarativos
`policies/*.yaml` como fuente can?nica (`argentgob.module_c.yaml_policies`).
Si no existen archivos YAML, degrada a los perfiles in-memory (`module_c.profiles`),
de modo que el enforcement nunca queda sin fuentes.

Uso:
    python -m scripts.seed_policies

Requiere que las migraciones ya se hayan aplicado (make start ejecuta el
migrator) y que DATABASE_URL apunte a la base correcta.
"""
import sys

from argentgob.db.connection import get_connection
from argentgob.module_c.yaml_policies import load_all, to_seed_rows
from argentgob.observability.logger import get_logger, setup_logging

log = get_logger("seed_policies")


def seed(environment: str | None = None, connection_factory=None) -> int:
    """Inserta las pol?ticas declarativas (YAML) en PostgreSQL.

    Retorna el total de filas insertadas. Usa UPSERT sobre la clave compuesta
    (policy_id, policy_version) y actualiza los campos editables.
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
                cur.execute(
                    """
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
                    """,
                    row,
                )
                insertadas += cur.rowcount
                log.info(
                    "policy_seeded",
                    policy_id=row["policy_id"],
                    profile=row["profile"],
                    tool=row["tool"],
                )

            # Los YAML son la fuente canonica: elimina las politicas del
            # mismo environment que ya no estan declaradas (pruning).
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


def main() -> None:
    setup_logging()
    try:
        total = seed()
        log.info("seed_completo", politicas_insertadas=total)
        print(f"Pol?ticas insertadas: {total}")
    except Exception as exc:  # noqa: BLE001
        log.error("seed_fallido", detail=str(exc))
        print(f"Error al cargar pol?ticas: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
