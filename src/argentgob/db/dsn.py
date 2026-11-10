"""Construcción canónica de DSN a partir de secretos montados (AV-02).

Reglas de AV-02:

- Un único secreto canónico por consumidor, montado como archivo; nunca se
  interpola la contraseña en la línea de `compose.yaml` ni se imprime su valor.
- Escape correcto de usuario, contraseña y nombre de base: una contraseña con
  caracteres reservados (`@`, `:`, `/`, `#`, `%`, espacio) debe funcionar.
- El rol de runtime (`argentgob_runtime`) es distinto del administrativo; el
  DSN de runtime se resuelve con `resolve_database_url()` por defecto.
- Si no hay archivos de secreto, se respeta `DATABASE_URL` (tests y CLI), de
  modo que el comportamiento histórico no cambia.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

DEFAULT_ROLE = "runtime"
SECRET_ENV_VARS = {
    "runtime": "ARGENTGOB_RUNTIME_PASSWORD_FILE",
    "dashboard": "ARGENTGOB_DASHBOARD_PASSWORD_FILE",
    "admin": "ARGENTGOB_ADMIN_PASSWORD_FILE",
}
URL_ENV_VARS = {
    "runtime": "DATABASE_RUNTIME_URL",
    "dashboard": "DATABASE_DASHBOARD_URL",
    "admin": "DATABASE_URL",
}
DB_USERS = {
    "runtime": "argentgob_runtime",
    "dashboard": "argentgob_dashboard",
    "admin": "argentgob_app",
}


def read_secret_file(path: str | Path | None) -> str | None:
    """Lee un secreto de archivo; `None` si la ruta es vacía o no existe.

    Se elimina un único salto de línea final (los secretos creados con `echo`
    lo arrastran). No se registra ni se retorna el contenido en logs.
    """
    if not path:
        return None
    candidate = Path(path)
    try:
        raw = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    value = raw.rstrip("\r\n")
    return value or None


def read_secret(env_var: str) -> str | None:
    """Resuelve un secreto por variable (`*_FILE`) o valor directo.

    El valor directo solo se acepta en entornos de desarrollo y se documenta
    como tal; el archivo tiene precedencia para no dejar secretos en el
    entorno del proceso.
    """
    from_file = read_secret_file(os.environ.get(f"{env_var}_FILE"))
    if from_file is not None:
        return from_file
    return os.environ.get(env_var) or None


def build_dsn(
    *,
    user: str,
    password: str | None,
    host: str,
    port: int | str,
    dbname: str,
    driver: str = "postgresql+psycopg",
) -> str:
    """Construye un DSN con escape de credenciales (T-A08).

    Nunca se concatena la contraseña sin codificar: `quote(..., safe='')`
    escapa `@`, `:`, `/`, `#`, `%`, `?` y espacios, que de otro modo rompen el
    parseo de la URL.
    """
    if not user or not host or not dbname:
        raise ValueError("user, host y dbname son obligatorios para el DSN")
    credentials = quote(user, safe="")
    if password:
        credentials = f"{credentials}:{quote(password, safe='')}"
    return f"{driver}://{credentials}@{host}:{int(port)}/{quote(dbname, safe='')}"


def resolve_database_url(*, role: str = DEFAULT_ROLE) -> str:
    """Retorna el DSN efectivo para un rol lógico (runtime/dashboard/admin).

    Precedencia (el secreto montado es la fuente can?nica):
    1. Archivo de secreto del rol + `ARGENTGOB_DB_{HOST,PORT,NAME,USER}`.
    2. `DATABASE_RUNTIME_URL` / `DATABASE_DASHBOARD_URL` / `DATABASE_URL`.

    El archivo gana sobre una URL heredada de `.env` para que el despliegue no
    dependa de credenciales embebidas en el entorno (AV-02).

    Lanza `RuntimeError` sin exponer valores si no hay ninguna fuente.
    """
    password = read_secret_file(os.environ.get(SECRET_ENV_VARS.get(role, ""), ""))
    if password is None:
        explicit = os.environ.get(URL_ENV_VARS.get(role, ""), "")
        if explicit:
            return explicit
        raise RuntimeError(
            f"sin DSN para el rol '{role}': configura {SECRET_ENV_VARS.get(role)} "
            f"o {URL_ENV_VARS.get(role)}"
        )
    user = os.environ.get("ARGENTGOB_DB_USER") or DB_USERS.get(role, DB_USERS["runtime"])
    return build_dsn(
        user=user,
        password=password,
        host=os.environ.get("ARGENTGOB_DB_HOST", "postgres"),
        port=os.environ.get("ARGENTGOB_DB_PORT", "5432"),
        dbname=os.environ.get("ARGENTGOB_DB_NAME", "argentgob"),
    )


def redact_dsn(url: str) -> str:
    """Devuelve el DSN sin credenciales, apto para diagnóstico."""
    if "://" not in url:
        return "<dsn>"
    scheme, _, rest = url.partition("://")
    if "@" not in rest:
        return f"{scheme}://<host>"
    _, _, tail = rest.rpartition("@")
    return f"{scheme}://<redacted>@{tail}"
