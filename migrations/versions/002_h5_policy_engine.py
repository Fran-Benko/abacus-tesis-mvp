"""002_h5_policy_engine

H5 — Policy Engine persistente.
Unidades 1 y 2: separa las tablas del policy engine, crea la vista
`v_governance_console` sanitizada y separa roles con grants mínimos.

Tablas:
- governance_events: eventos de tool call (identidad/contexto, correlación,
  clase/recurso/ambiente, digest, idempotencia, bytes, timestamps).
- policy_decisions: una decisión por evento, round-trip H5 completo.
- governance_policies: políticas ABAC versionadas (clave compuesta
  policy_id/policy_version), autoría, vigencia y digests.
- execution_results: resultados de ejecución, unicidad por evento.
- approval_requests, approval_resume_tokens: almacenamiento de H6/H7 (sin
  implementar sus servicios).
- audit_chain: cadena de auditoría (secuencia + digest).

Vista: v_governance_console sanitizada para dashboard y auditor.
Roles: argentgob_app (owner), argentgob_migrator, argentgob_auditor (lectura),
argentgob_policy_admin (versiona políticas, no ejecuta tools).

Revision ID: 002
Revises: 001
Create Date: 2026-09-18
"""
import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

_APP = "argentgob_app"


def upgrade() -> None:
    # ─── Governance events: ampliar tabla 001 con contexto H5 ─────────
    op.add_column(
        "governance_events",
        sa.Column("session_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("agent_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("agent_role", sa.String(64), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("operation_class", sa.String(32), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("resource", sa.String(256), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("environment", sa.String(32), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("payload_digest", sa.String(80), nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("payload_size_bytes", sa.Integer, nullable=True),
    )
    op.add_column(
        "governance_events",
        sa.Column("idempotency_key", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_governance_events_idempotency",
        "governance_events",
        ["idempotency_key"],
    )
    # Índice por traza/fecha de evento (Unidad 1.6).
    op.create_index(
        "ix_governance_events_recorded_at", "governance_events", ["recorded_at"]
    )

    # ─── Governance policies: versionar con clave compuesta H5 ────────
    # Versión positiva; clave compuesta policy_id/policy_version.
    op.add_column(
        "governance_policies",
        sa.Column("policy_version", sa.Integer, nullable=True),
    )
    # Poblar versión 1 para las filas existentes (migración no destructiva).
    op.execute(
        "UPDATE governance_policies SET policy_version = 1 "
        "WHERE policy_version IS NULL"
    )
    op.alter_column(
        "governance_policies", "policy_version", nullable=False, server_default="1"
    )
    op.create_check_constraint(
        "ck_policies_version_positive",
        "governance_policies",
        "policy_version > 0",
    )
    # Reemplazar PK simple por compuesta.
    op.drop_constraint(
        "governance_policies_pkey", "governance_policies", type_="primary"
    )
    op.create_primary_key(
        "governance_policies_pkey",
        "governance_policies",
        ["policy_id", "policy_version"],
    )
    op.add_column(
        "governance_policies",
        sa.Column("environment", sa.String(32), nullable=True),
    )
    op.add_column(
        "governance_policies",
        sa.Column("resource", sa.String(256), nullable=True),
    )
    op.add_column(
        "governance_policies",
        sa.Column("sensitivity_limit", sa.String(32), nullable=True),
    )
    op.add_column(
        "governance_policies",
        sa.Column("obligations", sa.Text, nullable=True),  # JSON array
    )
    op.add_column(
        "governance_policies",
        sa.Column("transform_spec", sa.Text, nullable=True),  # JSON object
    )
    op.add_column(
        "governance_policies",
        sa.Column("policy_digest", sa.String(80), nullable=True),
    )
    op.add_column(
        "governance_policies",
        sa.Column("authored_by", sa.String(128), nullable=True),
    )
    # Normalizar effect legacy (ALLOW->PASS, DENY->BLOCK) al vocabulario H5.
    op.execute(
        "UPDATE governance_policies SET effect = 'PASS' WHERE effect = 'ALLOW'"
    )
    op.execute(
        "UPDATE governance_policies SET effect = 'BLOCK' WHERE effect = 'DENY'"
    )
    op.create_check_constraint(
        "ck_policies_effect",
        "governance_policies",
        "effect IN ('PASS', 'BLOCK', 'HITL')",
    )
    op.create_check_constraint(
        "ck_policies_dates_coherent",
        "governance_policies",
        "valid_until IS NULL OR valid_until > valid_from",
    )
    # Índices ABAC/prioridad y vigencia (Unidad 1.6).
    op.create_index(
        "ix_policies_abac",
        "governance_policies",
        ["profile_name", "tool_name", "operation_class", "environment"],
    )
    op.create_index(
        "ix_policies_validity",
        "governance_policies",
        ["valid_from", "valid_until"],
    )

    # ─── Policy decisions (round-trip H5 completo) ──────────────────
    op.create_table(
        "policy_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("agent_id", sa.String(128), nullable=True),
        sa.Column("agent_role", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("operation_class", sa.String(32), nullable=False),
        sa.Column("resource", sa.String(256), nullable=True),
        sa.Column("environment", sa.String(32), nullable=True),
        sa.Column("payload_digest", sa.String(80), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=True),
        sa.Column("reason_codes", sa.Text, nullable=True),  # JSON array
        sa.Column("policy_id", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.Integer, nullable=True),
        sa.Column("policy_digest", sa.String(80), nullable=True),
        sa.Column("matched_policy_ids", sa.Text, nullable=True),  # JSON array
        sa.Column("effective_priority", sa.Integer, nullable=True),
        sa.Column("conflicts", sa.Text, nullable=True),  # JSON array
        sa.Column("obligations", sa.Text, nullable=True),  # JSON array
        sa.Column("transform_spec", sa.Text, nullable=True),  # JSON object
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_by_guardrail", sa.String(64), nullable=True),
        sa.Column("latency_pre_ms", sa.Float, nullable=True),
    )
    op.create_check_constraint(
        "ck_decisions_action",
        "policy_decisions",
        "action IN ('PASS', 'BLOCK', 'HITL')",
    )
    op.create_check_constraint(
        "ck_decisions_risk_level",
        "policy_decisions",
        "risk_level IN ('LOW', 'MEDIUM', 'HIGH')",
    )
    op.create_check_constraint(
        "ck_decisions_frontier",
        "policy_decisions",
        "policy_id IS NOT NULL OR "
        "(policy_version IS NULL AND policy_digest IS NULL)",
    )
    op.create_index(
        "ix_policy_decisions_agent_id", "policy_decisions", ["agent_id"]
    )
    op.create_index(
        "ix_policy_decisions_action_decided",
        "policy_decisions", ["action", "decided_at"],
    )

    # ─── Execution results ──────────────────────────────────────────
    op.create_table(
        "execution_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("trim_stats_id", sa.Integer, nullable=True),
        sa.Column("decision_id", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("error_digest", sa.String(80), nullable=True),
        sa.Column("result_digest", sa.String(80), nullable=True),
        sa.Column("latency_post_ms", sa.Float, nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_execution_results_event_id", "execution_results", ["event_id"]
    )
    op.create_foreign_key(
        "fk_execution_event_id", "execution_results", "policy_decisions",
        ["event_id"], ["event_id"],
    )
    op.create_foreign_key(
        "fk_execution_decision_id", "execution_results", "policy_decisions",
        ["decision_id"], ["decision_id"],
    )

    # ─── Approval requests (H6/H7 storage) ──────────────────────────
    op.create_table(
        "approval_requests",
        sa.Column("approval_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("decision_id", sa.String(64), nullable=True),
        sa.Column("agent_id", sa.String(128), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("argument_digest", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("argued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("decision", sa.String(32), nullable=True),
    )
    op.create_check_constraint(
        "ck_approval_state",
        "approval_requests",
        "state IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED')",
    )
    op.create_check_constraint(
        "ck_approval_dates",
        "approval_requests",
        "expires_at IS NULL OR expires_at > argued_at",
    )
    op.create_unique_constraint(
        "uq_approval_idempotency", "approval_requests", ["idempotency_key"]
    )
    op.create_index(
        "ix_approval_requests_state_expires",
        "approval_requests", ["state", "expires_at"],
    )
    op.create_index(
        "ix_approval_requests_argument_digest",
        "approval_requests", ["argument_digest"],
    )
    op.create_foreign_key(
        "fk_approval_event_id", "approval_requests", "policy_decisions",
        ["event_id"], ["event_id"],
    )

    # ─── Approval resume tokens (H6/H7 storage) ───────────────────
    op.create_table(
        "approval_resume_tokens",
        sa.Column("token_id", sa.String(64), primary_key=True),
        sa.Column("approval_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_token_state",
        "approval_resume_tokens",
        "state IN ('ACTIVE', 'REDEEMED', 'REVOKED', 'EXPIRED')",
    )
    op.create_index(
        "ix_approval_resume_tokens_approval",
        "approval_resume_tokens", ["approval_id"],
    )
    op.create_index(
        "ix_approval_resume_tokens_state_expires",
        "approval_resume_tokens", ["state", "expires_at"],
    )
    op.create_foreign_key(
        "fk_token_approval_id", "approval_resume_tokens", "approval_requests",
        ["approval_id"], ["approval_id"],
    )

    # ─── Audit chain ─────────────────────────────────────────────
    op.create_table(
        "audit_chain",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sequence", sa.BigInteger, nullable=False, unique=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("decision_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(16), nullable=True),
        sa.Column("chain_hash", sa.String(80), nullable=False),
        sa.Column("prev_hash", sa.String(80), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_audit_chain_event_id", "audit_chain", ["event_id"]
    )
    op.create_index(
        "ix_audit_chain_sequence", "audit_chain", ["sequence"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_audit_event_id", "audit_chain", "policy_decisions",
        ["event_id"], ["event_id"],
    )

    # ─── Vista sanitizada v_governance_console ─────────────────────
    _create_view()
    _create_roles_and_grants()


def _create_view() -> None:
    """Crea la vista v_governance_console (sanitizada, sin raw)."""
    op.execute(
        """
        CREATE VIEW v_governance_console AS
        SELECT
            d.decision_id,
            d.event_id,
            d.session_id,
            d.agent_id,
            d.agent_role,
            d.tool_name,
            d.operation_class,
            d.resource,
            d.environment,
            d.payload_digest,
            d.action,
            d.risk_level,
            d.reason_codes,
            d.policy_id,
            d.policy_version,
            d.effective_priority,
            d.conflicts,
            d.obligations,
            d.decided_at,
            d.expires_at,
            e.status,
            e.error_digest,
            e.result_digest,
            e.latency_post_ms,
            e.recorded_at
        FROM policy_decisions d
        LEFT JOIN execution_results e ON e.event_id = d.event_id
        """
    )


def _create_roles_and_grants() -> None:
    """Crea los roles H5 y otorga grants mínimos (Unidad 2)."""
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='argentgob_migrator') "
        "THEN CREATE ROLE argentgob_migrator NOLOGIN; END IF; END $$;"
    )
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='argentgob_auditor') "
        "THEN CREATE ROLE argentgob_auditor NOLOGIN; END IF; END $$;"
    )
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='argentgob_policy_admin') "
        "THEN CREATE ROLE argentgob_policy_admin NOLOGIN; END IF; END $$;"
    )
    # Revocar privilegios públicos sobre el schema (Unidad 2).
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON DATABASE argentgob FROM PUBLIC")
    # Auditor: solo lectura.
    op.execute("GRANT USAGE ON SCHEMA public TO argentgob_auditor")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO argentgob_auditor")
    op.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO argentgob_auditor")
    op.execute("GRANT SELECT ON v_governance_console TO argentgob_auditor")
    # Policy admin: versiona políticas, no ejecuta tools.
    op.execute("GRANT USAGE ON SCHEMA public TO argentgob_policy_admin")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON governance_policies "
        "TO argentgob_policy_admin"
    )
    # Migrator: DDL para migraciones.
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO argentgob_migrator")
    # App lee la vista y políticas; es owner de las tablas vía POSTGRES_USER.
    op.execute(f"GRANT SELECT ON v_governance_console TO {_APP}")
    op.execute(f"GRANT SELECT ON governance_policies TO {_APP}")


def downgrade() -> None:
    _drop_role_grants()
    op.execute("DROP VIEW IF EXISTS v_governance_console")
    op.drop_table("audit_chain")
    op.drop_table("approval_resume_tokens")
    op.drop_table("approval_requests")
    op.drop_table("execution_results")
    op.drop_index("ix_policy_decisions_action_decided", table_name="policy_decisions")
    op.drop_index("ix_policy_decisions_agent_id", table_name="policy_decisions")
    op.drop_table("policy_decisions")
    op.drop_index("ix_policies_validity", table_name="governance_policies")
    op.drop_index("ix_policies_abac", table_name="governance_policies")
    op.drop_constraint(
        "ck_policies_dates_coherent", "governance_policies", type_="check"
    )
    op.drop_constraint(
        "ck_policies_effect", "governance_policies", type_="check"
    )
    op.drop_column("governance_policies", "authored_by")
    op.drop_column("governance_policies", "policy_digest")
    op.drop_column("governance_policies", "transform_spec")
    op.drop_column("governance_policies", "obligations")
    op.drop_column("governance_policies", "sensitivity_limit")
    op.drop_column("governance_policies", "resource")
    op.drop_column("governance_policies", "environment")
    op.drop_constraint(
        "governance_policies_pkey", "governance_policies", type_="primary"
    )
    op.create_primary_key(
        "governance_policies_pkey", "governance_policies", ["policy_id"]
    )
    op.drop_constraint(
        "ck_policies_version_positive", "governance_policies", type_="check"
    )
    op.drop_column("governance_policies", "policy_version")
    op.drop_index(
        "ix_governance_events_recorded_at", table_name="governance_events"
    )
    op.drop_constraint(
        "uq_governance_events_idempotency", "governance_events", type_="unique"
    )
    op.drop_column("governance_events", "idempotency_key")
    op.drop_column("governance_events", "payload_size_bytes")
    op.drop_column("governance_events", "payload_digest")
    op.drop_column("governance_events", "environment")
    op.drop_column("governance_events", "resource")
    op.drop_column("governance_events", "operation_class")
    op.drop_column("governance_events", "agent_role")
    op.drop_column("governance_events", "agent_id")
    op.drop_column("governance_events", "session_id")


def _drop_role_grants() -> None:
    """Revoca grants de migración (los roles se dejan; no se borran)."""
    op.execute("REVOKE CREATE ON SCHEMA public FROM argentgob_migrator")
    op.execute("REVOKE USAGE ON SCHEMA public FROM argentgob_migrator")
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON governance_policies "
        "FROM argentgob_policy_admin"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM argentgob_policy_admin")
    op.execute("REVOKE SELECT ON ALL SEQUENCES IN SCHEMA public FROM argentgob_auditor")
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM argentgob_auditor")
    op.execute("REVOKE USAGE ON SCHEMA public FROM argentgob_auditor")
    op.execute(f"REVOKE SELECT ON v_governance_console FROM {_APP}")
    op.execute(f"REVOKE SELECT ON governance_policies FROM {_APP}")
    op.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")