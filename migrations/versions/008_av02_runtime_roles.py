"""008_av02_runtime_roles

AV-02 — Roles de runtime con mínimo privilegio.

Hasta 007 todos los servicios usaban `argentgob_app`, que es superusuario
(`rolsuper`, `rolcreatedb`, `rolcreaterole`). Esta migración separa el rol de
migración/administración del de ejecución:

- `argentgob_runtime` (agent/API): escribe evidencia de gobernanza, ejecuta
  tools gobernadas y lee políticas/artefactos predictivos. Sin DDL, sin
  privilegios de superusuario.
- `argentgob_dashboard`: solo lectura sobre evidencia y vistas sanitizadas, más
  el UPDATE condicional de `approval_requests` que exige la resolución H8.

Los roles se crean `NOLOGIN` y sin contraseña: el inicializador
(`argentgob.db.init`) habilita el login y fija la contraseña desde el archivo de
secreto, de modo que ninguna credencial viaja en `compose.yaml` ni en el
repositorio. Si el secreto no existe (CLI, tests), los roles permanecen
`NOLOGIN` y el comportamiento histórico no cambia.

Revision ID: 008
Revises: 007
Create Date: 2026-11-10
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_RUNTIME = "argentgob_runtime"
_DASHBOARD = "argentgob_dashboard"

_RUNTIME_TABLES = {
    "governance_policies": "SELECT",
    "governance_decisions": "SELECT, INSERT",
    "governance_events": "SELECT, INSERT",
    "policy_decisions": "SELECT, INSERT",
    "execution_results": "SELECT, INSERT",
    "approval_requests": "SELECT, INSERT, UPDATE",
    "approval_resume_tokens": "SELECT, INSERT, UPDATE",
    "risk_model_versions": "SELECT",
    "risk_predictions": "SELECT, INSERT",
    "model_drift_reports": "SELECT, INSERT",
}

_DASHBOARD_TABLES = {
    "governance_policies": "SELECT",
    "governance_decisions": "SELECT",
    "governance_events": "SELECT",
    "policy_decisions": "SELECT",
    "execution_results": "SELECT",
    "approval_requests": "SELECT, UPDATE",
    "approval_resume_tokens": "SELECT",
    "audit_chain": "SELECT",
    "risk_model_versions": "SELECT",
    "risk_predictions": "SELECT",
    "model_drift_reports": "SELECT",
}

_VIEWS = ("v_governance_console", "v_approval_console", "v_risk_evidence")


def upgrade() -> None:
    _create_role(_RUNTIME)
    _create_role(_DASHBOARD)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_RUNTIME}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_DASHBOARD}")
    for table, privileges in _RUNTIME_TABLES.items():
        op.execute(f"GRANT {privileges} ON {table} TO {_RUNTIME}")
    # audit_chain tiene trigger append-only: el runtime solo agrega y lee.
    op.execute(f"GRANT SELECT, INSERT ON audit_chain TO {_RUNTIME}")
    for table, privileges in _DASHBOARD_TABLES.items():
        op.execute(f"GRANT {privileges} ON {table} TO {_DASHBOARD}")
    for view in _VIEWS:
        _grant_view(view, _RUNTIME)
        _grant_view(view, _DASHBOARD)
    # Secuencias de las tablas con identidad/serial usadas por el runtime.
    for sequence in (
        "governance_events_id_seq",
        "execution_results_id_seq",
        "audit_chain_id_seq",
    ):
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {sequence} TO {_RUNTIME}")
    # Defensa en profundidad: ningún rol de runtime hereda privilegios de DDL.
    for role in (_RUNTIME, _DASHBOARD):
        op.execute(f"ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE")


def downgrade() -> None:
    for sequence in (
        "governance_events_id_seq",
        "execution_results_id_seq",
        "audit_chain_id_seq",
    ):
        op.execute(f"REVOKE USAGE, SELECT ON SEQUENCE {sequence} FROM {_RUNTIME}")
    for view in _VIEWS:
        op.execute(f"REVOKE SELECT ON {view} FROM {_RUNTIME}")
        op.execute(f"REVOKE SELECT ON {view} FROM {_DASHBOARD}")
    for table, privileges in _DASHBOARD_TABLES.items():
        op.execute(f"REVOKE {privileges} ON {table} FROM {_DASHBOARD}")
    op.execute(f"REVOKE SELECT, INSERT ON audit_chain FROM {_RUNTIME}")
    for table, privileges in _RUNTIME_TABLES.items():
        op.execute(f"REVOKE {privileges} ON {table} FROM {_RUNTIME}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_RUNTIME}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_DASHBOARD}")


def _create_role(role: str) -> None:
    """Crea el rol NOLOGIN si no existe (idempotente)."""
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles "
        f"WHERE rolname='{role}') "
        f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;"
    )


def _grant_view(view: str, role: str) -> None:
    """Otorga SELECT sobre una vista si existe (006/007 pueden variar)."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_views "
        f"WHERE schemaname='public' AND viewname='{view}') "
        f"THEN GRANT SELECT ON {view} TO {role}; END IF; END $$;"
    )
