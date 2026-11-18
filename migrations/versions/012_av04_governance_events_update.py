"""012_av04_governance_events_update

AV-04 — El rol de runtime necesita `UPDATE` sobre `governance_events`.

`AuditStore.record_result()` registra el resultado post-ejecución con un upsert
idempotente (`INSERT ... ON CONFLICT (event_id) DO UPDATE`). PostgreSQL exige
privilegio `UPDATE` para la rama `DO UPDATE` aunque la fila ya exista. La
migración 008 solo otorgó `SELECT, INSERT`, por lo que el registro del
resultado fallaba con `permission denied for table governance_events` y se
perdía la evidencia del post-hook (observabilidad, no enforcement).

Se otorga `UPDATE` acotado a esa tabla. El rol sigue sin `DELETE` ni DDL.

Revision ID: 012
Revises: 011
Create Date: 2026-11-18
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

_RUNTIME = "argentgob_runtime"


def upgrade() -> None:
    op.execute(f"GRANT UPDATE ON governance_events TO {_RUNTIME}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ON governance_events FROM {_RUNTIME}")