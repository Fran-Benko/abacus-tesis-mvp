"""
scripts/seed_policies.py

Carga las políticas ABAC iniciales en PostgreSQL a partir de los perfiles
declarados in-memory (módulo argentgob.module_c.profiles). Genera una política
ALLOW por cada herramienta permitida de cada perfil.

Uso:
    python -m scripts.seed_policies

Requiere que las migraciones ya se hayan aplicado (make start ejecuta el
migrator) y que DATABASE_URL apunte a la base correcta.
"""
import sys

from argentgob.db.connection import get_connection
from argentgob.module_c.profiles import PROFILES
from argentgob.observability.logger import get_logger, setup_logging

setup_logging()
log = get_logger("seed_policies")

# Clase de operación por herramienta (MVP: todas son de lectura/consulta).
_OPERATION_CLASS = {
    "duckduckgo_news": "EXTERNAL_SEND",
    "stock_price": "READ",
    "crypto_price": "READ",
}


def seed() -> int:
    """Inserta las políticas ALLOW de todos los perfiles. Retorna el total insertado."""
    conn = get_connection()
    insertadas = 0
    try:
        with conn.cursor() as cur:
            for profile in PROFILES.values():
                for tool in profile.allowed_tools:
                    policy_id = f"POL-{profile.name}-{tool}"
                    op_class = _OPERATION_CLASS.get(tool, "READ")
                    cur.execute(
                        """
                        INSERT INTO governance_policies (
                            policy_id, profile_name, tool_name,
                            operation_class, effect, priority
                        ) VALUES (
                            %(policy_id)s, %(profile)s, %(tool)s,
                            %(op_class)s, 'ALLOW', 50
                        )
                        ON CONFLICT (policy_id) DO NOTHING
                        """,
                        {
                            "policy_id": policy_id,
                            "profile": profile.name,
                            "tool": tool,
                            "op_class": op_class,
                        },
                    )
                    insertadas += cur.rowcount
                    log.info(
                        "policy_seeded",
                        policy_id=policy_id,
                        profile=profile.name,
                        tool=tool,
                    )
    finally:
        conn.close()
    return insertadas


def main() -> None:
    try:
        total = seed()
        log.info("seed_completo", politicas_insertadas=total)
        print(f"Políticas insertadas: {total}")
    except Exception as exc:  # noqa: BLE001
        log.error("seed_fallido", detail=str(exc))
        print(f"Error al cargar políticas: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
