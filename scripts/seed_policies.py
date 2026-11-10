"""scripts/seed_policies.py

Compatibilidad: el seed canónico vive en `argentgob.module_c.policy_seeder`
para que el inicializador (`python -m argentgob.db.init`) lo ejecute como paso
obligatorio (AV-02). Este script conserva la interfaz histórica.

Uso:
    python -m scripts.seed_policies
"""
import sys

from argentgob.module_c.policy_seeder import seed
from argentgob.observability.logger import get_logger, setup_logging

log = get_logger("seed_policies")

__all__ = ["main", "seed"]


def main() -> None:
    setup_logging()
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
