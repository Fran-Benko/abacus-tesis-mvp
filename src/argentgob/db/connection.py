"""
Manejo de conexión a PostgreSQL con psycopg3.

La URL de SQLAlchemy usa el prefijo `postgresql+psycopg://`; psycopg3 nativo
requiere `postgresql://`. Esta capa convierte el prefijo cuando corresponde.
"""
import os

import psycopg

from argentgob.core.config import get_settings
from argentgob.db.dsn import resolve_database_url

DEFAULT_DB_ROLE = "runtime"


def _db_role() -> str:
    """Rol lógico del proceso (`ARGENTGOB_DB_ROLE`, por defecto runtime).

    El dashboard corre con el rol de solo lectura `dashboard`; sin esta
    selección caería al DSN de runtime, cuyo secreto no está montado en ese
    contenedor, y terminaría en el `DATABASE_URL` de `.env` (localhost).
    """
    return (os.environ.get("ARGENTGOB_DB_ROLE") or DEFAULT_DB_ROLE).strip().lower()


def get_db_url(role: str | None = None) -> str:
    """Construye una URL nativa de psycopg3 (sin el prefijo `+psycopg`).

    AV-02: prioriza el DSN del rol resuelto desde el secreto montado; sin
    secreto (CLI/tests) conserva `DATABASE_URL`/Settings.

    `role` permite pedir explícitamente otro rol lógico (p. ej. `admin` para
    escrituras de registro de modelos que el rol de runtime no puede hacer).
    """
    try:
        return normalize_db_url(resolve_database_url(role=role or _db_role()))
    except RuntimeError:
        return normalize_db_url(get_settings().database_url)


def normalize_db_url(url: str) -> str:
    """Convierte una URL estilo SQLAlchemy a una URL nativa de psycopg3."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


def get_connection(role: str | None = None) -> psycopg.Connection:
    """Retorna una conexión sincrónica psycopg3 con autocommit activado."""
    return psycopg.connect(get_db_url(role=role), autocommit=True)
