"""
Manejo de conexión a PostgreSQL con psycopg3.

La URL de SQLAlchemy usa el prefijo `postgresql+psycopg://`; psycopg3 nativo
requiere `postgresql://`. Esta capa convierte el prefijo cuando corresponde.
"""
import psycopg

from argentgob.core.config import get_settings


def get_db_url() -> str:
    """Construye una URL nativa de psycopg3 (sin el prefijo `+psycopg`)."""
    settings = get_settings()
    url = settings.database_url
    return normalize_db_url(url)


def normalize_db_url(url: str) -> str:
    """Convierte una URL estilo SQLAlchemy a una URL nativa de psycopg3."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


def get_connection() -> psycopg.Connection:
    """Retorna una conexión sincrónica psycopg3 con autocommit activado."""
    return psycopg.connect(get_db_url(), autocommit=True)
