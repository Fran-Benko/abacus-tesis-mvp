"""Inicializador reproducible del stack (AV-02).

Un único punto de entrada que deja la base lista para admitir trabajo:

1. `alembic upgrade head` (DDL versionado).
2. Habilitación de login de los roles de runtime con la contraseña del archivo
   de secreto (nunca en `compose.yaml` ni en logs).
3. Seed canónico de políticas desde `policies/*.yaml`.
4. Verificación de postcondiciones: revisión en head, catálogo sembrado y
   privilegios mínimos de los roles.

Cualquier fallo propaga exit != 0: el servicio `agent` no debe admitir runs si
el inicializador no terminó correctamente (T-A01/T-A04).

Uso:
    python -m argentgob.db.init
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from argentgob.db.dsn import (
    DB_USERS,
    SECRET_ENV_VARS,
    read_secret_file,
    resolve_database_url,
)
from argentgob.observability.logger import get_logger, setup_logging

log = get_logger("db_init")

EXPECTED_HEAD = "011"
MIN_POLICIES = 1


class InitError(RuntimeError):
    """Fallo de inicialización: el stack no debe admitir trabajo."""


@dataclass
class InitReport:
    """Resultado sanitizado del inicializador (sin secretos ni DSN)."""

    revision: str = ""
    policies: int = 0
    roles_provisioned: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


def _connection_factory() -> Callable[[], Any]:
    """Conexión administrativa (DDL/seed) resuelta desde el secreto."""
    import psycopg

    from argentgob.db.connection import normalize_db_url

    url = normalize_db_url(resolve_database_url(role="admin"))
    return lambda: psycopg.connect(url, autocommit=True)


def run_migrations() -> None:
    """Aplica `alembic upgrade head` con la URL administrativa."""
    from alembic import command
    from alembic.config import Config

    from argentgob.db.connection import normalize_db_url

    config = Config(str(_repo_root() / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_repo_root() / "migrations")
    )
    os.environ["DATABASE_URL"] = normalize_db_url(
        resolve_database_url(role="admin")
    )
    command.upgrade(config, "head")


def provision_roles(connection_factory: Callable[[], Any]) -> list[str]:
    """Habilita LOGIN y fija la contraseña de los roles de runtime.

    Solo actúa si existe el archivo de secreto del rol; sin secreto los roles
    quedan `NOLOGIN` (CLI/tests) y no se inventa una credencial.
    """
    from psycopg import sql

    provisioned: list[str] = []
    conn = connection_factory()
    try:
        for role in ("runtime", "dashboard"):
            password = read_secret_file(os.environ.get(SECRET_ENV_VARS[role], ""))
            if password is None:
                log.info("role_password_absent", role=role)
                continue
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        sql.Identifier(DB_USERS[role]),
                        sql.Literal(password),
                    )
                )
            provisioned.append(DB_USERS[role])
            log.info("role_provisioned", role=DB_USERS[role])
    finally:
        conn.close()
    return provisioned


def seed_policies(connection_factory: Callable[[], Any]) -> int:
    """Siembra el catálogo declarativo y devuelve las filas escritas."""
    from argentgob.module_c.policy_seeder import seed

    return seed(
        environment=os.environ.get("ENVIRONMENT", "LOCAL"),
        connection_factory=connection_factory,
    )


def verify(connection_factory: Callable[[], Any]) -> InitReport:
    """Comprueba las postcondiciones del inicializador (T-A01/T-A08)."""
    report = InitReport()
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            report.revision = (row[0] if row else "") or ""
            cur.execute("SELECT count(*) FROM governance_policies")
            report.policies = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT rolname, rolsuper, rolcreatedb, rolcreaterole
                FROM pg_roles WHERE rolname = ANY(%s)
                """,
                (list(DB_USERS.values()),),
            )
            roles = {row[0]: row[1:] for row in cur.fetchall()}
    finally:
        conn.close()

    report.checks["revision_en_head"] = report.revision == EXPECTED_HEAD
    report.checks["catalogo_sembrado"] = report.policies >= MIN_POLICIES
    for role in ("runtime", "dashboard"):
        name = DB_USERS[role]
        flags = roles.get(name)
        report.checks[f"rol_{role}_existe"] = flags is not None
        # Solo los privilegios administrativos (super/createdb/createrole).
        report.checks[f"rol_{role}_sin_superusuario"] = bool(flags) and not any(
            flags[:3]
        )
    return report


def initialize(
    *,
    connection_factory: Callable[[], Any] | None = None,
    migrate: bool = True,
) -> InitReport:
    """Ejecuta el inicializador completo y lanza `InitError` si falla."""
    if migrate:
        run_migrations()
    factory = connection_factory or _connection_factory()
    provisioned = provision_roles(factory)
    written = seed_policies(factory)
    report = verify(factory)
    report.roles_provisioned = provisioned
    log.info(
        "db_init_complete",
        revision=report.revision,
        policies=report.policies,
        policies_written=written,
        roles=provisioned,
        ok=report.ok,
    )
    if not report.ok:
        failed = sorted(name for name, ok in report.checks.items() if not ok)
        raise InitError(f"postcondiciones incumplidas: {', '.join(failed)}")
    return report


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    """Entrypoint del inicializador: exit 0 solo con postcondiciones OK."""
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        report = initialize()
    except Exception as exc:  # noqa: BLE001 - el fallo debe propagarse
        log.error("db_init_failed", error_type=type(exc).__name__, detail=str(exc))
        print(f"ERROR: inicialización fallida ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(
        "OK: inicialización completa "
        f"(revision={report.revision}, policies={report.policies}, "
        f"roles={len(report.roles_provisioned)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
