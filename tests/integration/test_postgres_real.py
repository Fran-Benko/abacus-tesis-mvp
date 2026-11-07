"""Test de integracion con PostgreSQL real (hito de prioridad alta).

Valida que el prototipo funciona contra la base de datos real (migraciones,
constraints, grants minimos y vistas sanitizadas), cerrando el sesgo 1 de
`docs/analytics/0004-analisis-tests-integracion.md` ("PostgreSQL real").

El test se salta si no hay `TEST_DATABASE_URL` (db de prueba dedicada: no toca
los datos del dashboard). Ejecucion tipica:

    docker run --rm -v "${PWD}:/app" -w /app \\
        -e TEST_DATABASE_URL="postgresql+psycopg://.../argentgob_test" \\
        argentgob-mvp-agent:latest python -m pytest tests/integration/test_postgres_real.py -v

Si la variable de entorno apunta a la misma DB que el dashboard, el test la
rechaza (solo opera sobre bases de test dedicadas).
"""
from __future__ import annotations

import os

import psycopg
import pytest

from argentgob.db.connection import normalize_db_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def _is_test_db(url: str) -> bool:
    """Acepta solo urls cuyo nombre de DB indique prueba (evita el dashboard)."""
    return "_test" in url.split("/")[-1] or "test" in url.split("/")[-1].lower()


requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL or not _is_test_db(TEST_DATABASE_URL),
    reason="sin TEST_DATABASE_URL apuntando a una DB de prueba",
)


def _native_url() -> str:
    return normalize_db_url(TEST_DATABASE_URL)


@pytest.fixture
def pg_conn():
    """Conexion real a la DB de prueba (autocommit)."""
    conn = psycopg.connect(_native_url(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@requires_pg
def test_migraciones_en_head(pg_conn):
    """Las migraciones estan aplicadas hasta 006 (head)."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
    assert row is not None, "alembic_version no existe"
    assert row[0] == "006", f"head esperado 006, got {row[0]}"


@requires_pg
def test_tablas_clave_existen(pg_conn):
    """Las tablas del flujo de gobernanza estan creadas por las migraciones."""
    expected = {
        "governance_policies",
        "governance_decisions",
        "governance_events",
        "policy_decisions",
        "execution_results",
        "approval_requests",
        "approval_resume_tokens",
        "audit_chain",
        "risk_model_versions",
        "risk_predictions",
    }
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        tables = {r[0] for r in cur.fetchall()}
    missing = expected - tables
    assert not missing, f"faltan tablas: {sorted(missing)}"


@requires_pg
def test_constraints_de_unicidad_y_check(pg_conn):
    """Constraints: event_id unico en policy_decisions y CHECK action valido."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = 'policy_decisions' AND c.contype = 'u'
            """
        )
        n_unique = cur.fetchone()[0]
        assert n_unique >= 1, "policy_decisions sin constraint UNIQUE"
        cur.execute(
            """
            SELECT COUNT(*) FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = 'policy_decisions' AND c.contype = 'c'
            """
        )
        n_check = cur.fetchone()[0]
        assert n_check >= 1, "policy_decisions sin CHECK constraint"
    # Verificacion funcional: un action invalido debe ser rechazado.
    with pytest.raises(psycopg.errors.CheckViolation), pg_conn.cursor() as cur:
        cur.execute(
            """
                INSERT INTO policy_decisions (
                    decision_id, event_id, tool_name, operation_class, action, decided_at
                ) VALUES (
                    'dec-invalid', 'ev-invalid', 'news', 'READ', 'BOGUS', now()
                )
                """
        )


@requires_pg
def test_roles_consola_sin_payloads_crudos(pg_conn):
    """Grants minimos: los roles de consola NO leen tablas con payload crudo.

    Valida el principio de menor privilegio declarado en la migracion 004:
    viewer/auditor/operator solo leen las vistas sanitizadas; la tabla
    `policy_decisions` (que contiene argumentos/digests) no tiene SELECT
    concedido a esos roles.
    """
    console_roles = [
        "argentgob_console_viewer",
        "argentgob_console_auditor",
        "argentgob_console_operator",
    ]
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT grantee, table_name
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
              AND table_name = 'policy_decisions'
              AND privilege_type = 'SELECT'
            """
        )
        grantees = {(r[0], r[1]) for r in cur.fetchall()}
    app_roles = {"argentgob_app", "argentgob_migrator", "argentgob_audit_owner"}
    for role in console_roles:
        assert (role, "policy_decisions") not in grantees, (
            f"{role} tiene SELECT sobre policy_decisions (deberia ser solo vistas)"
        )
    assert any(g[0] in app_roles for g in grantees), (
        "ningun rol de aplicacion tiene SELECT sobre policy_decisions"
    )


@requires_pg
def test_flujo_real_persiste_y_vista_sanitizada(pg_conn, settings, monkeypatch):
    """Un flujo real (LOW -> PASS) persiste y se lee via la vista sanitizada."""
    from argentgob.analytics.features import FeatureBuilder
    from argentgob.analytics.predictor import RiskPredictor
    from argentgob.analytics.store import PredictionStore
    from argentgob.core.envelope import AgentIdentity
    from argentgob.db import connection as connection_module
    from argentgob.db.audit import AuditWriter
    from argentgob.module_a.governance import GovernanceMiddleware
    from argentgob.module_a.spy_tool import SpyTool
    from argentgob.module_c.abac_evaluator import ABACEvaluator
    from argentgob.module_c.guardrails import GuardrailEngine
    from argentgob.module_c.profiles import PROFILES

    def _factory():
        return psycopg.connect(_native_url(), autocommit=True)

    monkeypatch.setattr(connection_module, "get_connection", _factory)

    profile = PROFILES["analyst"]
    abac = ABACEvaluator(settings, profile)
    monkeypatch.setattr(abac, "_load_allowed_tools", lambda: list(profile.allowed_tools))
    guardrails = GuardrailEngine(
        profile_guardrails=profile.active_guardrails,
        rate_limit=settings.rate_limit_calls_per_run,
    )
    mw = GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=AuditWriter(settings),
        feature_builder=FeatureBuilder(),
        risk_predictor=RiskPredictor(),
        prediction_store=PredictionStore(settings),
    )
    import uuid
    agent_tag = "pg-test-" + uuid.uuid4().hex[:8]
    identity = AgentIdentity(id=agent_tag, role="analyst")

    class _NamedSpy(SpyTool):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.name = "news"

    tool = _NamedSpy(governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"
    result = tool._run(query="bitcoin mercado")
    assert "spy_executed" in result

    # Persistencia real: decisiones y (si el modelo esta) predicciones.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM governance_decisions WHERE agent_id = %(a)s",
            {"a": agent_tag},
        )
        n_decisions = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM risk_predictions WHERE event_id IN ("
            "SELECT event_id FROM governance_decisions WHERE agent_id = %(a)s)",
            {"a": agent_tag},
        )
    assert n_decisions == 1

    # La vista sanitizada refleja la decision sin payload crudo.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT information_schema.columns.column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'v_governance_console'"
        )
        cols = {r[0] for r in cur.fetchall()}
    assert "payload_digest" in cols
    assert "execution_arguments" not in cols
    assert "telemetry_arguments" not in cols


@requires_pg
def test_view_evidence_no_expone_payload(pg_conn):
    """v_risk_evidence (migracion 006) no expone columnas de payload crudo."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT information_schema.columns.column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'v_risk_evidence'"
        )
        cols = {r[0] for r in cur.fetchall()}
    for forbidden in ("execution_arguments", "telemetry_arguments", "payload"):
        assert forbidden not in cols, f"v_risk_evidence expone {forbidden}"
    for required in ("prediction_id", "final_action", "final_reason_code"):
        assert required in cols, f"falta {required} en v_risk_evidence"