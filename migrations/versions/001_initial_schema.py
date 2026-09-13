"""001_initial_schema

Crea el schema mínimo del MVP:
- governance_policies: políticas ABAC por perfil
- governance_decisions: decisiones del PEP (PASS/BLOCK)
- governance_events: registro de resultados de ejecución de tool calls

Revision ID: 001
Revises:
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── Políticas ABAC ────────────────────────────────────────────
    op.create_table(
        "governance_policies",
        sa.Column("policy_id", sa.String(64), primary_key=True),
        sa.Column("profile_name", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("operation_class", sa.String(32), nullable=False),
        sa.Column("effect", sa.String(8), nullable=False),  # ALLOW | DENY
        sa.Column("priority", sa.Integer, nullable=False, server_default="50"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )

    # ─── Decisiones de gobernanza ──────────────────────────────────
    op.create_table(
        "governance_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("agent_role", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("operation_class", sa.String(32), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),  # PASS | BLOCK
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=True),
        sa.Column("payload_digest", sa.String(80), nullable=False),
        sa.Column("blocked_by_guardrail", sa.String(64), nullable=True),
        sa.Column("latency_pre_ms", sa.Float, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )

    # ─── Resultados de ejecución ───────────────────────────────────
    op.create_table(
        "governance_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("decision_id", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=True),  # SUCCESS | FAILED
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("latency_post_ms", sa.Float, nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )

    # Índices para las consultas del dashboard.
    op.create_index("ix_decisions_agent_id", "governance_decisions", ["agent_id"])
    op.create_index("ix_decisions_decided_at", "governance_decisions", ["decided_at"])
    op.create_index("ix_events_tool_name", "governance_events", ["tool_name"])


def downgrade() -> None:
    op.drop_index("ix_events_tool_name", table_name="governance_events")
    op.drop_index("ix_decisions_decided_at", table_name="governance_decisions")
    op.drop_index("ix_decisions_agent_id", table_name="governance_decisions")
    op.drop_table("governance_events")
    op.drop_table("governance_decisions")
    op.drop_table("governance_policies")
