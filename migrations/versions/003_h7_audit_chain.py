"""003_h7_audit_chain

H7 — Auditoría detectable ante manipulación (Unidad 2: protección y verificador).

Refuerza la protección de `audit_chain` (creada en 002, hoy vacía):

- Columna `payload`: persiste el payload canónico (JSON) de cada entrada para
  que el verificador pueda recalcular el hash y detectar alteración de valores.
- Trigger append-only: impide UPDATE, DELETE y TRUNCATE sobre `audit_chain`
  (la protección real, ya que el app se conecta como superusuario/owner del
  esquema y los grants por sí solos no bastan).
- Reasignación de ownership: `audit_chain` pasa a ser propiedad de un rol
  dedicado `argentgob_audit_owner` (NOLOGIN), de modo que el app NO es owner
  de la tabla.
- Grants mínimos: el app solo INSERT + SELECT (append + lectura); el auditor
  solo SELECT; se revoca UPDATE/DELETE/TRUNCATE a los roles de runtime.

Límite declarado (H7): sin checkpoint externo no se puede garantizar la
detección de una reescritura completa o de un truncado final coherente por
parte del owner/superusuario. La cadena es tamper-evident, no inmutable.

Revision ID: 003
Revises: 002
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

_APP = "argentgob_app"
_AUDIT_OWNER = "argentgob_audit_owner"
_AUDITOR = "argentgob_auditor"
_POLICY_ADMIN = "argentgob_policy_admin"
_MIGRATOR = "argentgob_migrator"

_TRIGGER_FN = "audit_chain_append_only"
_TRIGGER_DML = "trg_audit_chain_append_only"
_TRIGGER_TRUNCATE = "trg_audit_chain_no_truncate"


def upgrade() -> None:
    _add_payload_column()
    _create_audit_owner_role()
    _reassign_ownership()
    _create_append_only_trigger()
    _apply_grants()


def downgrade() -> None:
    _revoke_grants()
    _drop_append_only_trigger()
    # Reasignar ownership de vuelta al app (superusuario) para no dejar la
    # tabla huérfana al revertir.
    op.execute(f"ALTER TABLE audit_chain OWNER TO {_APP}")
    op.drop_column("audit_chain", "payload")


def _add_payload_column() -> None:
    """Persiste el payload canónico (JSON) para permitir el recálculo del hash."""
    op.add_column(
        "audit_chain",
        sa.Column("payload", sa.Text, nullable=True),
    )


def _create_audit_owner_role() -> None:
    """Crea el rol propietario dedicado de audit_chain (NOLOGIN)."""
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles "
        f"WHERE rolname='{_AUDIT_OWNER}') "
        f"THEN CREATE ROLE {_AUDIT_OWNER} NOLOGIN; END IF; END $$;"
    )


def _reassign_ownership() -> None:
    """El app deja de ser owner de audit_chain (pasa al rol dedicado)."""
    op.execute(f"ALTER TABLE audit_chain OWNER TO {_AUDIT_OWNER}")


def _create_append_only_trigger() -> None:
    """Trigger que impide UPDATE/DELETE/TRUNCATE sobre audit_chain."""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_TRIGGER_FN}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_chain is append-only: UPDATE/DELETE/TRUNCATE forbidden';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS {_TRIGGER_DML} ON audit_chain
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_DML}
        BEFORE UPDATE OR DELETE ON audit_chain
        FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FN}()
        """
    )
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS {_TRIGGER_TRUNCATE} ON audit_chain
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_TRUNCATE}
        BEFORE TRUNCATE ON audit_chain
        FOR EACH STATEMENT EXECUTE FUNCTION {_TRIGGER_FN}()
        """
    )


def _apply_grants() -> None:
    """Grants mínimos: app solo INSERT+SELECT, auditor solo SELECT."""
    # App: append + lectura (no UPDATE/DELETE/TRUNCATE).
    op.execute(f"GRANT SELECT, INSERT ON audit_chain TO {_APP}")
    # Auditor: solo lectura.
    op.execute(f"GRANT SELECT ON audit_chain TO {_AUDITOR}")
    # Revocar explícitamente UPDATE/DELETE/TRUNCATE a roles de runtime.
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_chain FROM {_APP}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_chain FROM {_AUDITOR}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_chain FROM {_POLICY_ADMIN}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_chain FROM {_MIGRATOR}")


def _revoke_grants() -> None:
    """Revoca los grants de migración (los roles se dejan; no se borran)."""
    op.execute(f"REVOKE SELECT, INSERT ON audit_chain FROM {_APP}")
    op.execute(f"REVOKE SELECT ON audit_chain FROM {_AUDITOR}")


def _drop_append_only_trigger() -> None:
    """Elimina el trigger y la función append-only."""
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_DML} ON audit_chain")
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_TRUNCATE} ON audit_chain")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FN}()")