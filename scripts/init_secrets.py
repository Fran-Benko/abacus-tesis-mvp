"""Genera los secretos locales del stack (AV-02) sin imprimir sus valores.

Crea, si faltan, los archivos que `compose.yaml` monta:

- `.secrets/postgres_password` (administrativo, ya existente en el stack)
- `.secrets/runtime_password` (rol `argentgob_runtime`)
- `.secrets/dashboard_password` (rol `argentgob_dashboard`)
- `.secrets/service_token` (cabecera `x-argentgob-service-token`)

Los archivos existentes **no** se sobrescriben: rotar un secreto es una acción
explícita del operador. Permisos 0600 en sistemas POSIX.

Uso:
    python scripts/init_secrets.py
    python scripts/init_secrets.py --rotate runtime_password
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS_DIR = ROOT / ".secrets"

# Longitud suficiente para tokens urlsafe; nunca se imprime el valor.
GENERATED = {
    "runtime_password": 24,
    "dashboard_password": 24,
    "service_token": 32,
}


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows: el permiso POSIX no aplica


def ensure_secrets(*, rotate: set[str] | None = None) -> dict[str, str]:
    """Crea los secretos faltantes; retorna el estado por archivo."""
    rotate = rotate or set()
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    for name, length in GENERATED.items():
        path = SECRETS_DIR / name
        if path.exists() and name not in rotate:
            status[name] = "existente"
            continue
        _write(path, secrets.token_urlsafe(length))
        status[name] = "rotado" if name in rotate else "creado"
    admin = SECRETS_DIR / "postgres_password"
    status["postgres_password"] = "existente" if admin.exists() else "ausente"
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera secretos locales AV-02")
    parser.add_argument(
        "--rotate",
        action="append",
        default=[],
        choices=sorted(GENERATED),
        help="regenera el secreto indicado (acción explícita)",
    )
    args = parser.parse_args(argv)
    status = ensure_secrets(rotate=set(args.rotate))
    for name, state in status.items():
        print(f"  {name}: {state}")
    if status.get("postgres_password") == "ausente":
        print(
            "Falta .secrets/postgres_password: créalo antes de levantar el stack.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
