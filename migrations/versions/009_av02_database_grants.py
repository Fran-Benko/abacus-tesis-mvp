"""009_av02_database_grants

AV-02 — Privilegios a nivel de base para los roles de runtime.

La migración 002 revocó `ALL ON DATABASE argentgob FROM PUBLIC`, de modo que un
rol nuevo no puede ni conectarse. Esta migración otorga el mínimo necesario:

- `argentgob_runtime`: CONNECT + TEMP (el hijo del run usa cursores temporales
  de psycopg en memoria, no tablas temporales persistentes).
- `argentgob_dashboard`: CONNECT.

No se otorga CREATE ni ownership: el DDL sigue reservado al rol administrativo.

Revision ID: 009
Revises: 008
Create Date: 2026-11-10
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

_DB = "argentgob"
_RUNTIME = "argentgob_runtime"
_DASHBOARD = "argentgob_dashboard"


def upgrade() -> None:
    op.execute(f"GRANT CONNECT, TEMPORARY ON DATABASE {_DB} TO {_RUNTIME}")
    op.execute(f"GRANT CONNECT ON DATABASE {_DB} TO {_DASHBOARD}")


def downgrade() -> None:
    op.execute(f"REVOKE CONNECT, TEMPORARY ON DATABASE {_DB} FROM {_RUNTIME}")
    op.execute(f"REVOKE CONNECT ON DATABASE {_DB} FROM {_DASHBOARD}")
