"""005_risk_predictions

Fase 4 — Integración operacional. Persistencia de la capa predictiva.

Tablas:
- risk_model_versions: registro de versiones del modelo predictivo
  (model_id, model_version, algorithm, feature_schema_version,
  training_dataset_version, trained_at, metrics_json, calibration_method,
  decision_thresholds, artifact_digest, status).
- risk_predictions: predicciones de riesgo por tool call (prediction_id,
  event_id, decision_id, model_id, model_version, predicted_risk_class,
  risk_probability, class_probabilities, inference_latency_ms,
  feature_schema_version, predicted_at).

Grants: el rol de aplicación `argentgob_app` (owner) escribe ambas tablas; los
roles de consola (viewer/auditor/operator) solo leen `risk_predictions` para
visualización (Fase 5). Se sigue el patrón de grants mínimos de 002/004.

Revision ID: 005
Revises: 004
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

_APP = "argentgob_app"
_VIEWER = "argentgob_console_viewer"
_AUDITOR = "argentgob_console_auditor"
_OPERATOR = "argentgob_console_operator"


def upgrade() -> None:
    # ─── Versiones del modelo predictivo ───────────────────────────
    op.create_table(
        "risk_model_versions",
        sa.Column("model_id", sa.String(64), primary_key=True),
        sa.Column("model_version", sa.String(32), primary_key=True),
        sa.Column("algorithm", sa.String(32), nullable=False),
        sa.Column("feature_schema_version", sa.String(16), nullable=False),
        sa.Column("training_dataset_version", sa.String(64), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", sa.Text, nullable=True),
        sa.Column("calibration_method", sa.String(32), nullable=True),
        sa.Column("decision_thresholds", sa.Text, nullable=True),
        sa.Column("artifact_digest", sa.String(80), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )

    # ─── Predicciones de riesgo por tool call ──────────────────────
    op.create_table(
        "risk_predictions",
        sa.Column("prediction_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("decision_id", sa.String(64), nullable=True),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("predicted_risk_class", sa.String(16), nullable=False),
        sa.Column("risk_probability", sa.Float, nullable=False),
        sa.Column("class_probabilities", sa.Text, nullable=True),
        sa.Column("inference_latency_ms", sa.Float, nullable=True),
        sa.Column("feature_schema_version", sa.String(16), nullable=False),
        sa.Column("predicted_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_risk_predictions_event_id", "risk_predictions", ["event_id"]
    )
    op.create_index(
        "ix_risk_predictions_predicted_at", "risk_predictions", ["predicted_at"]
    )

    # ─── Grants mínimos ────────────────────────────────────────────
    # App (owner) escribe ambas tablas.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON risk_model_versions TO {_APP}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON risk_predictions TO {_APP}")
    # Consola: solo lectura de predicciones (visualización Fase 5).
    op.execute(f"GRANT SELECT ON risk_predictions TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON risk_predictions TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON risk_predictions TO {_OPERATOR}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON risk_predictions FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON risk_predictions FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT ON risk_predictions FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON risk_predictions FROM {_APP}")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON risk_model_versions FROM {_APP}")
    op.drop_index("ix_risk_predictions_predicted_at", table_name="risk_predictions")
    op.drop_index("ix_risk_predictions_event_id", table_name="risk_predictions")
    op.drop_table("risk_predictions")
    op.drop_table("risk_model_versions")