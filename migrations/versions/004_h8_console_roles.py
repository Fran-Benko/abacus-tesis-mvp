"""004_h8_console_roles

H8 — Consola sanitizada y resolución autorizada (Unidad 2: identidad y
autorización).

Crea los roles de aplicación de la consola como roles de base de datos
NOLOGIN (viewer/auditor/policy_admin/operator) y otorga grants mínimos:

- `argentgob_console_viewer`: solo lectura de la vista sanitizada
  `v_governance_console` y de los campos autorizados de `approval_requests`.
- `argentgob_console_auditor`: lectura de la vista + resolución de approvals
  (UPDATE condicional sobre `approval_requests`). NO escribe en
  `policy_decisions` ni en `execution_results`.
- `argentgob_console_policy_admin`: versiona políticas (fuera del alcance de
  la UI, se deja el rol por paridad con AppRole).
- `argentgob_console_operator`: solo recuperación no destructiva (SELECT).

Además crea la vista `v_approval_console` (sanitizada, sin argumentos en
claro) que la consola usa para el detalle HITL.

Los roles de aplicación (AppRole) son separados de estos roles DB: la consola
mapea AppRole -> rol DB para la conexión de lectura/resolución.

Revision ID: 004
Revises: 003
Create Date: 2026-09-19
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

_APP = "argentgob_app"
_VIEWER = "argentgob_console_viewer"
_AUDITOR = "argentgob_console_auditor"
_POLICY_ADMIN = "argentgob_console_policy_admin"
_OPERATOR = "argentgob_console_operator"


def upgrade() -> None:
    _create_roles()
    _create_approval_view()
    _apply_grants()


def downgrade() -> None:
    _revoke_grants()
    op.execute("DROP VIEW IF EXISTS v_approval_console")


def _create_roles() -> None:
    """Crea los roles de aplicación de la consola (NOLOGIN)."""
    for role in (_VIEWER, _AUDITOR, _POLICY_ADMIN, _OPERATOR):
        op.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles "
            f"WHERE rolname='{role}') "
            f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;"
        )


def _create_approval_view() -> None:
    """Vista sanitizada de approvals (sin argumentos en claro, INV-05)."""
    op.execute(
        """
        CREATE VIEW v_approval_console AS
        SELECT
            ar.approval_id,
            ar.event_id,
            ar.decision_id,
            ar.agent_id,
            ar.tool_name,
            ar.argument_digest,
            ar.idempotency_key,
            ar.state,
            ar.argued_at,
            ar.expires_at,
            ar.resolved_at,
            ar.resolved_by,
            ar.decision,
            d.resource,
            d.environment,
            d.risk_level,
            d.reason_codes,
            d.policy_id,
            d.policy_version,
            d.operation_class
        FROM approval_requests ar
        LEFT JOIN policy_decisions d ON d.decision_id = ar.decision_id
        """
    )


def _apply_grants() -> None:
    """Grants mínimos por rol de aplicación de la consola."""
    # Viewer: solo lectura de la vista sanitizada y de approvals.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON v_governance_console TO {_VIEWER}")
    op.execute(f"GRANT SELECT ON v_approval_console TO {_VIEWER}")
    # Auditor: lectura + resolución condicional de approvals (UPDATE).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON v_governance_console TO {_AUDITOR}")
    op.execute(f"GRANT SELECT ON v_approval_console TO {_AUDITOR}")
    op.execute(
        f"GRANT SELECT, UPDATE ON approval_requests TO {_AUDITOR}"
    )
    # Policy admin: versiona políticas (paridad con AppRole).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_POLICY_ADMIN}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON governance_policies "
        f"TO {_POLICY_ADMIN}"
    )
    # Operator: solo recuperación no destructiva (SELECT).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_OPERATOR}")
    op.execute(f"GRANT SELECT ON v_governance_console TO {_OPERATOR}")
    op.execute(f"GRANT SELECT ON v_approval_console TO {_OPERATOR}")
    # App lee la vista de approvals (owner de las tablas vía POSTGRES_USER).
    op.execute(f"GRANT SELECT ON v_approval_console TO {_APP}")


def _revoke_grants() -> None:
    """Revoca los grants de migración (los roles se dejan; no se borran)."""
    op.execute(f"REVOKE SELECT ON v_governance_console FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT ON v_approval_console FROM {_VIEWER}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_VIEWER}")
    op.execute(f"REVOKE SELECT ON v_governance_console FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT ON v_approval_console FROM {_AUDITOR}")
    op.execute(f"REVOKE SELECT, UPDATE ON approval_requests FROM {_AUDITOR}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_AUDITOR}")
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE ON governance_policies "
        f"FROM {_POLICY_ADMIN}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_POLICY_ADMIN}")
    op.execute(f"REVOKE SELECT ON v_governance_console FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON v_approval_console FROM {_OPERATOR}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_OPERATOR}")
    op.execute(f"REVOKE SELECT ON v_approval_console FROM {_APP}")