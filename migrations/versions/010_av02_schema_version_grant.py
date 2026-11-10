"""010_av02_schema_version_grant

AV-02 — El rol de runtime necesita leer la revisión de esquema.

`/ready` y el inicializador verifican `alembic_version`; sin `SELECT` sobre esa
tabla el rol de runtime no puede confirmar que el esquema está en head y el
servicio queda en 503. Se otorga solo lectura (nunca escritura: el DDL sigue
siendo del rol administrativo).

Revision ID: 010
Revises: 009
Create Date: 2026-11-10
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

_RUNTIME = "argentgob_runtime"
_DASHBOARD = "argentgob_dashboard"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON alembic_version TO {_RUNTIME}")
    op.execute(f"GRANT SELECT ON alembic_version TO {_DASHBOARD}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON alembic_version FROM {_RUNTIME}")
    op.execute(f"REVOKE SELECT ON alembic_version FROM {_DASHBOARD}")
