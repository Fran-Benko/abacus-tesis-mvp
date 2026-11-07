"""007_model_drift_reports

Hito de prioridad media — monitoreo de deriva del modelo.

Crea la tabla `model_drift_reports`, que persiste los reportes de PSI
(Population Stability Index) entre la distribucion de referencia (baseline del
corpus) y la distribucion observada en produccion (clases de riesgo predichas
y features canonicas).

Invariante INV-05: persiste solo metadatos, distribuciones de probabilidad,
PSI y severidad. Nunca payloads crudos (los digests viajan, no los
argumentos).

Grants minimos: el rol de aplicacion `argentgob_app` (owner) escribe; los
roles de consola (viewer/auditor/operator) solo leen para visualizacion en el
dashboard (mismo patron de 005/006).

Revision ID: 007
Revises: 006
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

_APP = "argentgob_app"
_VIEWER = "argentgob_console_viewer"
_AUDITOR = "argentgob_console_auditor"
_OPERATOR = "argentgob_console_operator"


def upgrade() -> None:
    op.create_table(
        "model_drift_reports",
        sa.Column("report_id", sa.String(80), primary_key=True),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("feature_schema_version", sa.String(16), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("class_psi", sa.Float, nullable=False),
        sa.Column("feature_psi", sa.Text, nullable=True),
        sa.Column("class_distribution_baseline", sa.Text, nullable=True),
        sa.Column("class_distribution_observed", sa.Text, nullable=True),
        sa.Column("severity_class", sa.String(16), nullable=False),
        sa.Column("severity_global", sa.String(16), nullable=False),
        sa.Column("sample_size_baseline", sa.Integer, nullable=False),
        sa.Column("sample_size_observed", sa.Integer, nullable=False),
        sa.Column("features_compared", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_model_drift_reports_generated_at",
        "model_drift_reports",
        ["generated_at"],
    )

    # Grants minimos: app escribe; consola solo lee.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON model_drift_reports TO {_APP}")
    op.execute(f"GRANT SELECT ON model_drift_reports TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON model_drift_reports TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON model_drift_reports TO {_OPERATOR}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON model_drift_reports FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON model_drift_reports FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT ON model_drift_reports FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON model_drift_reports FROM {_APP}")
    op.drop_index(
        "ix_model_drift_reports_generated_at", table_name="model_drift_reports"
    )
    op.drop_table("model_drift_reports")
