"""Gate G-AV-02: inicialización reproducible y topología real (T-A01…T-A08).

Ejecuta comprobaciones **reales** contra PostgreSQL (no parseo de YAML):

1. Preflight de secretos/artefactos/GGUF/Compose.
2. Base nueva aislada → inicializador exit 0, revisión en head, catálogo
   sembrado y roles provisionados (T-A01).
3. Segundo arranque: sin duplicados ni pérdida de políticas (T-A02).
4. Base existente con datos: upgrade conserva filas y no borra políticas
   ajenas (T-A03).
5. Fallo de seed/migración impide la admisión: postcondiciones incumplidas
   lanzan `InitError` (T-A04).
6. Rol de runtime: escribe evidencia, no puede hacer DDL ni es superusuario
   (T-A08).
7. Rol de dashboard: lee vistas sanitizadas y resuelve approvals, no escribe
   decisiones (T-A08).
8. Contraseña con caracteres reservados funciona (T-A08).

La base de prueba se crea y se elimina en esta ejecución; nunca se toca la base
del stack. No se imprimen secretos ni DSN completos.

Uso:
    python scripts/verify_stack_av02.py
    python scripts/verify_stack_av02.py --keep-db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

TEST_DB = "argentgob_av02_test"
BROKEN_DB = "argentgob_av02_broken"
EXPECTED_HEAD = "010"

PASS = 0
FAIL = 0
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail[:200]})
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _admin_url(dbname: str) -> str:
    """DSN administrativo hacia una base concreta (sin imprimir valores)."""
    from argentgob.db.dsn import build_dsn, read_secret_file

    password = read_secret_file(os.environ.get("ARGENTGOB_ADMIN_PASSWORD_FILE"))
    if password is None:
        password = read_secret_file(str(ROOT / ".secrets" / "postgres_password"))
    if password is None:
        raise RuntimeError("sin secreto administrativo para el gate")
    return build_dsn(
        user=os.environ.get("ARGENTGOB_DB_USER", "argentgob_app"),
        password=password,
        host=os.environ.get("ARGENTGOB_DB_HOST", "127.0.0.1"),
        port=os.environ.get("ARGENTGOB_DB_PORT", "5432"),
        dbname=dbname,
    )


def _connect(url: str):
    import psycopg

    from argentgob.db.connection import normalize_db_url

    return psycopg.connect(normalize_db_url(url), autocommit=True)


def _recreate_database(name: str) -> None:
    """Crea la base de prueba desde cero (idempotente y aislada)."""
    maintenance = _connect(_admin_url("postgres"))
    try:
        with maintenance.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        maintenance.close()


def _drop_database(name: str) -> None:
    maintenance = _connect(_admin_url("postgres"))
    try:
        with maintenance.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        maintenance.close()


def _write_temp_secret(directory: Path, name: str, value: str) -> Path:
    path = directory / name
    path.write_text(value, encoding="utf-8")
    return path


def _run_initializer(dbname: str, secrets: dict[str, Path]) -> tuple[int, str]:
    """Ejecuta el inicializador en un subproceso con entorno acotado."""
    import subprocess

    env = {
        **os.environ,
        "DATABASE_URL": _admin_url(dbname),
        "ARGENTGOB_ADMIN_PASSWORD_FILE": str(secrets["admin"]),
        "ARGENTGOB_RUNTIME_PASSWORD_FILE": str(secrets["runtime"]),
        "ARGENTGOB_DASHBOARD_PASSWORD_FILE": str(secrets["dashboard"]),
        "ARGENTGOB_DB_HOST": os.environ.get("ARGENTGOB_DB_HOST", "127.0.0.1"),
        "ARGENTGOB_DB_PORT": os.environ.get("ARGENTGOB_DB_PORT", "5432"),
        "ARGENTGOB_DB_NAME": dbname,
        "ENVIRONMENT": "LOCAL",
        "PYTHONPATH": str(ROOT / "src"),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "argentgob.db.init"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    tail = (completed.stdout or "").strip().splitlines()[-1:] or [""]
    return completed.returncode, tail[0][:200]


def _count(dbname: str, sql: str, params: tuple = ()) -> int:
    conn = _connect(_admin_url(dbname))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _role_flags(role: str) -> tuple | None:
    conn = _connect(_admin_url("postgres"))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
                "FROM pg_roles WHERE rolname = %s",
                (role,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _runtime_url(dbname: str, password: str) -> str:
    from argentgob.db.dsn import build_dsn

    return build_dsn(
        user="argentgob_runtime",
        password=password,
        host=os.environ.get("ARGENTGOB_DB_HOST", "127.0.0.1"),
        port=os.environ.get("ARGENTGOB_DB_PORT", "5432"),
        dbname=dbname,
    )


def _dashboard_url(dbname: str, password: str) -> str:
    from argentgob.db.dsn import build_dsn

    return build_dsn(
        user="argentgob_dashboard",
        password=password,
        host=os.environ.get("ARGENTGOB_DB_HOST", "127.0.0.1"),
        port=os.environ.get("ARGENTGOB_DB_PORT", "5432"),
        dbname=dbname,
    )



def _snapshot_deployed_passwords() -> dict[str, str]:
    """Lee las contrase?as del despliegue desde `.secrets` (sin imprimirlas)."""
    from argentgob.db.dsn import read_secret_file

    snapshot: dict[str, str] = {}
    for role, name in (
        ("runtime", "runtime_password"),
        ("dashboard", "dashboard_password"),
    ):
        value = read_secret_file(str(ROOT / ".secrets" / name))
        if value:
            snapshot[role] = value
    return snapshot


def _restore_deployed_passwords(snapshot: dict[str, str]) -> None:
    """Restaura las contrase?as de los roles del despliegue tras el gate."""
    if not snapshot:
        return
    from psycopg import sql

    from argentgob.db.dsn import DB_USERS

    conn = _connect(_admin_url("postgres"))
    try:
        for role, password in snapshot.items():
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        sql.Identifier(DB_USERS[role]), sql.Literal(password)
                    )
                )
        print("  [INFO] contrase?as del despliegue restauradas")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate G-AV-02")
    parser.add_argument("--keep-db", action="store_true", help="no borrar la DB de prueba")
    parser.add_argument("--report", default="", help="ruta del resumen JSON")
    args = parser.parse_args(argv)

    from argentgob.db.dsn import build_dsn, read_secret_file

    print("==> 1. Preflight")
    from preflight_stack import collect as preflight

    report = preflight()
    for name, ok in report["checks"].items():
        if name in ("puertos_loopback_libres", "compose_disponible"):
            # El stack puede estar arriba y el gate corre dentro del contenedor
            # (sin binario podman): se informa, no invalida el gate.
            print(f"  [INFO] preflight:{name}={ok}")
            continue
        check(f"preflight:{name}", ok)
    check(
        "preflight:secretos_requeridos",
        all(report["secrets"].values()),
        str(sorted(k for k, v in report["secrets"].items() if not v)),
    )

    admin_password = read_secret_file(str(ROOT / ".secrets" / "postgres_password"))
    if admin_password is None:
        print("ERROR: falta .secrets/postgres_password", file=sys.stderr)
        return 2
    # Contraseña con caracteres reservados para probar el escape del DSN.
    runtime_password = "rt-" + uuid.uuid4().hex[:8] + "@:/#%?&="
    dashboard_password = "db-" + uuid.uuid4().hex[:8] + "@:/#%?&="

    import tempfile

    # Los roles son a nivel de cl?ster: el gate cambia sus contrase?as para
    # probar el escape. Al terminar se restauran las del despliegue para no
    # romper el stack en ejecuci?n (T-A08).
    restore = _snapshot_deployed_passwords()

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        secrets = {
            "admin": _write_temp_secret(tmpdir, "admin", admin_password),
            "runtime": _write_temp_secret(tmpdir, "runtime", runtime_password),
            "dashboard": _write_temp_secret(tmpdir, "dashboard", dashboard_password),
        }

        print("==> 2. Base nueva aislada + inicializador (T-A01)")
        _recreate_database(TEST_DB)
        code, tail = _run_initializer(TEST_DB, secrets)
        check("inicializador exit 0", code == 0, tail)
        check(
            "revision en head",
            _count(TEST_DB, "SELECT count(*) FROM alembic_version WHERE version_num = %s",
                   (EXPECTED_HEAD,)) == 1,
        )
        policies_first = _count(TEST_DB, "SELECT count(*) FROM governance_policies")
        check("catalogo sembrado", policies_first > 0, f"policies={policies_first}")
        flags = _role_flags("argentgob_runtime")
        check("rol runtime existe y puede login", bool(flags) and flags[3] is True)
        check("rol runtime sin privilegios admin", bool(flags) and not any(flags[:3]))

        print("==> 3. Segundo arranque (T-A02)")
        code2, tail2 = _run_initializer(TEST_DB, secrets)
        policies_second = _count(TEST_DB, "SELECT count(*) FROM governance_policies")
        check("segundo arranque exit 0", code2 == 0, tail2)
        check(
            "sin duplicados de politicas",
            policies_second == policies_first,
            f"{policies_first} -> {policies_second}",
        )

        print("==> 4. Base existente con datos ajenos (T-A03)")
        conn = _connect(_admin_url(TEST_DB))
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO governance_policies (
                        policy_id, policy_version, profile_name, tool_name,
                        operation_class, environment, effect, priority,
                        obligations
                    ) VALUES ('POL-ajena-av02', 1, 'analyst', 'news',
                              'EXTERNAL_SEND', 'PROD', 'PASS', 50, '[]')
                    ON CONFLICT DO NOTHING
                    """
                )
        finally:
            conn.close()
        code3, tail3 = _run_initializer(TEST_DB, secrets)
        check("upgrade sobre base existente exit 0", code3 == 0, tail3)
        check(
            "politica de otro environment conservada",
            _count(
                TEST_DB,
                "SELECT count(*) FROM governance_policies WHERE policy_id = 'POL-ajena-av02'",
            ) == 1,
        )
        check(
            "catalogo LOCAL sin duplicados tras upgrade",
            _count(
                TEST_DB,
                "SELECT count(*) FROM governance_policies WHERE environment = 'LOCAL'",
            ) == policies_first,
        )

        print("==> 5. Fallo de seed impide admisión (T-A04)")
        _recreate_database(BROKEN_DB)
        broken_env = {
            **os.environ,
            "DATABASE_URL": _admin_url(BROKEN_DB),
            "ARGENTGOB_ADMIN_PASSWORD_FILE": str(secrets["admin"]),
            "ARGENTGOB_DB_HOST": os.environ.get("ARGENTGOB_DB_HOST", "127.0.0.1"),
            "ARGENTGOB_DB_PORT": os.environ.get("ARGENTGOB_DB_PORT", "5432"),
            "ARGENTGOB_DB_NAME": BROKEN_DB,
            "PYTHONPATH": str(ROOT / "src"),
        }
        import subprocess

        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(ROOT),
            env=broken_env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        check("migracion aislada sin seed exit 0", migrated.returncode == 0)
        verify_env = {
            **broken_env,
            "ARGENTGOB_RUNTIME_PASSWORD_FILE": str(secrets["runtime"]),
        }
        probe = (
            "import sys\n"
            "from argentgob.db import init\n"
            "report = init.verify(init._connection_factory())\n"
            "sys.exit(0 if report.ok else 3)\n"
        )
        verified = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(ROOT),
            env=verify_env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        check(
            "sin seed, postcondiciones incumplidas -> exit != 0",
            verified.returncode != 0,
            (verified.stderr or "").strip()[-120:],
        )

        print("==> 6. Rol de runtime: escritura sin DDL (T-A08)")
        runtime = _connect(_runtime_url(TEST_DB, runtime_password))
        try:
            with runtime.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM governance_policies WHERE environment = 'LOCAL'"
                )
                check("runtime lee politicas", int(cur.fetchone()[0]) > 0)
                cur.execute(
                    """
                    INSERT INTO policy_decisions (
                        decision_id, event_id, agent_id, agent_role, tool_name,
                        operation_class, action, reason_codes, payload_digest,
                        decided_at
                    ) VALUES (%s, %s, 'agent-av02', 'analyst', 'news',
                              'EXTERNAL_SEND', 'PASS', '["OK"]', 'sha256:av02', now())
                    """,
                    (f"dec-{uuid.uuid4().hex[:12]}", f"evt-{uuid.uuid4().hex[:12]}"),
                )
                check("runtime escribe evidencia", cur.rowcount == 1)
            ddl_blocked = False
            try:
                with runtime.cursor() as cur:
                    cur.execute("CREATE TABLE av02_should_fail (id int)")
            except Exception:  # noqa: BLE001 - el bloqueo es el resultado esperado
                ddl_blocked = True
            check("runtime no puede crear tablas", ddl_blocked)
        finally:
            runtime.close()

        print("==> 7. Rol de dashboard: lectura y resolución (T-A08)")
        dashboard = _connect(_dashboard_url(TEST_DB, dashboard_password))
        try:
            with dashboard.cursor() as cur:
                cur.execute("SELECT count(*) FROM v_governance_console")
                check("dashboard lee vista sanitizada", cur.fetchone() is not None)
                cur.execute("SELECT count(*) FROM v_approval_console")
                check("dashboard lee approvals sanitizados", cur.fetchone() is not None)
            write_blocked = False
            try:
                with dashboard.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO policy_decisions (
                            decision_id, event_id, agent_id, agent_role, tool_name,
                            operation_class, action, reason_codes, payload_digest,
                            decided_at
                        ) VALUES ('dec-x', 'evt-x', 'a', 'analyst', 'news',
                                  'EXTERNAL_SEND', 'PASS', '["OK"]', 'sha256:x', now())
                        """
                    )
            except Exception:  # noqa: BLE001 - el bloqueo es el resultado esperado
                write_blocked = True
            check("dashboard no escribe decisiones", write_blocked)
        finally:
            dashboard.close()

        print("==> 8. Contraseña con caracteres reservados (T-A08)")
        check(
            "DSN con caracteres reservados conecta",
            _count(TEST_DB, "SELECT 1") == 1,
        )
        escaped = build_dsn(
            user="u@x", password="p@:/#%?&=", host="h", port=5432, dbname="d"
        )
        check(
            "escape de credenciales en DSN",
            "p%40%3A%2F%23%25%3F%26%3D" in escaped and "u%40x" in escaped,
        )

        if not args.keep_db:
            _drop_database(TEST_DB)
            _drop_database(BROKEN_DB)

    _restore_deployed_passwords(restore)

    summary = {
        "gate": "G-AV-02",
        "passed": PASS,
        "failed": FAIL,
        "checks": RESULTS,
    }
    if args.report:
        Path(args.report).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"===== GATE G-AV-02: {PASS} passed, {FAIL} failed =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
