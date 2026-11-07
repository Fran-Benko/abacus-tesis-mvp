"""006_risk_evidence_view

Fase 5 — Interfaz y evidencia académica.

Crea la vista `v_risk_evidence`, que cruza cada predicción persistida
(`risk_predictions`, migración 005) con la decisión final de gobierno
(`governance_decisions`) para permitir la trazabilidad completa
corpus → modelo → predicción → decisión → visualización.

La vista es sanitizada (INV-05): expone metadatos, probabilidades y digests,
nunca `execution_arguments` ni el payload crudo.

Grants: los roles de consola (viewer/auditor/operator) solo leen la vista; el
rol de aplicación la lee además de escribir las tablas base.

Revision ID: 006
Revises: 005
Create Date: 2026-11-07
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

_APP = "argentgob_app"
_VIEWER = "argentgob_console_viewer"
_AUDITOR = "argentgob_console_auditor"
_OPERATOR = "argentgob_console_operator"


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW v_risk_evidence AS
        SELECT
            p.prediction_id,
            p.event_id,
            p.decision_id,
            p.model_id,
            p.model_version,
            p.predicted_risk_class,
            p.risk_probability,
            p.class_probabilities,
            p.inference_latency_ms,
            p.feature_schema_version,
            p.predicted_at,
            d.action        AS final_action,
            d.reason_code   AS final_reason_code,
            d.tool_name,
            d.agent_role,
            d.operation_class,
            d.decided_at
        FROM risk_predictions p
        LEFT JOIN governance_decisions d ON d.event_id = p.event_id
        """
    )

    op.execute(f"GRANT SELECT ON v_risk_evidence TO {_APP}")
    op.execute(f"GRANT SELECT ON v_risk_evidence TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON v_risk_evidence TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON v_risk_evidence TO {_OPERATOR}")
    # La vista cruza risk_predictions: los roles de consola necesitan leerla.
    op.execute(f"GRANT SELECT ON risk_predictions TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON risk_predictions TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON risk_predictions TO {_OPERATOR}")
    # Metadatos de versión del modelo (trazabilidad de la evidencia).
    op.execute(f"GRANT SELECT ON risk_model_versions TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON risk_model_versions TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON risk_model_versions TO {_OPERATOR}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON risk_model_versions FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON risk_model_versions FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT ON risk_model_versions FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT ON risk_predictions FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON risk_predictions FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT ON risk_predictions FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT ON v_risk_evidence FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON v_risk_evidence FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT ON v_risk_evidence FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT ON v_risk_evidence FROM {_APP}")
    op.execute("DROP VIEW IF EXISTS v_risk_evidence")
