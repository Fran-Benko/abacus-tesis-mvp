"""011_av04_risk_predictions_update

AV-04 — El rol de runtime necesita `UPDATE` sobre `risk_predictions`.

`PredictionStore.record_prediction()` usa un upsert idempotente
(`ON CONFLICT (prediction_id) DO UPDATE`) para vincular la predicción con el
`decision_id` una vez que la decisión existe. PostgreSQL exige privilegio
`UPDATE` para la rama `DO UPDATE`, aunque la fila ya exista. La migración 008
solo otorgó `SELECT, INSERT`, por lo que cada upsert fallaba con
`permission denied for table risk_predictions` y la predicción quedaba sin
`decision_id` (pérdida de trazabilidad, no de enforcement).

Se otorga `UPDATE` acotado a esa tabla. El rol sigue sin `DELETE` ni DDL.

Revision ID: 011
Revises: 010
Create Date: 2026-11-11
"""
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

_RUNTIME = "argentgob_runtime"


def upgrade() -> None:
    op.execute(f"GRANT UPDATE ON risk_predictions TO {_RUNTIME}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ON risk_predictions FROM {_RUNTIME}")
