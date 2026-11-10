"""Tests de AV-02: DSN desde secretos, inicializador y readiness (T-A01…T-A08).

Sin red ni PostgreSQL: usan conexiones falsas y archivos temporales. La
topología real se valida en `scripts/verify_stack_av02.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argentgob.db import dsn
from argentgob.db.init import InitError, InitReport, initialize, verify
from argentgob.live.readiness import StackReadiness


# ── DSN y secretos ───────────────────────────────────────────────────
def test_build_dsn_escapa_credenciales():
    url = dsn.build_dsn(
        user="u@x", password="p@:/#%?&=", host="postgres", port=5432, dbname="argentgob"
    )
    assert url.startswith("postgresql+psycopg://u%40x:p%40%3A%2F%23%25%3F%26%3D@")
    assert url.endswith(":5432/argentgob")


def test_build_dsn_sin_password_no_agrega_dos_puntos():
    url = dsn.build_dsn(user="u", password=None, host="h", port=1, dbname="d")
    assert url == "postgresql+psycopg://u@h:1/d"


def test_build_dsn_rechaza_campos_vacios():
    with pytest.raises(ValueError):
        dsn.build_dsn(user="", password="x", host="h", port=1, dbname="d")


def test_read_secret_file_quita_salto_final(tmp_path: Path):
    path = tmp_path / "secret"
    path.write_text("valor\n", encoding="utf-8")
    assert dsn.read_secret_file(path) == "valor"
    assert dsn.read_secret_file(tmp_path / "ausente") is None
    assert dsn.read_secret_file("") is None


def test_resolve_database_url_usa_url_explicita_sin_secreto(monkeypatch):
    monkeypatch.delenv("ARGENTGOB_RUNTIME_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("DATABASE_RUNTIME_URL", "postgresql://explicita")
    assert dsn.resolve_database_url(role="runtime") == "postgresql://explicita"


def test_resolve_database_url_secreto_gana_sobre_url_heredada(monkeypatch, tmp_path):
    """El secreto montado es can?nico: una URL de `.env` no lo pisa (AV-02)."""
    secret = tmp_path / "runtime"
    secret.write_text("p@ss", encoding="utf-8")
    monkeypatch.setenv("ARGENTGOB_RUNTIME_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("DATABASE_RUNTIME_URL", "postgresql://localhost/heredada")
    monkeypatch.setenv("ARGENTGOB_DB_HOST", "postgres")
    url = dsn.resolve_database_url(role="runtime")
    assert "heredada" not in url
    assert "@postgres:5432/" in url


def test_resolve_database_url_desde_archivo(monkeypatch, tmp_path: Path):
    secret = tmp_path / "runtime"
    secret.write_text("p@ss", encoding="utf-8")
    monkeypatch.delenv("DATABASE_RUNTIME_URL", raising=False)
    monkeypatch.setenv("ARGENTGOB_RUNTIME_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("ARGENTGOB_DB_HOST", "postgres")
    monkeypatch.setenv("ARGENTGOB_DB_NAME", "argentgob")
    url = dsn.resolve_database_url(role="runtime")
    assert "argentgob_runtime:p%40ss@postgres:5432/argentgob" in url


def test_resolve_database_url_sin_fuentes_falla_sin_filtrar(monkeypatch):
    monkeypatch.delenv("DATABASE_RUNTIME_URL", raising=False)
    monkeypatch.delenv("ARGENTGOB_RUNTIME_PASSWORD_FILE", raising=False)
    with pytest.raises(RuntimeError) as exc:
        dsn.resolve_database_url(role="runtime")
    assert "ARGENTGOB_RUNTIME_PASSWORD_FILE" in str(exc.value)


def test_redact_dsn_oculta_credenciales():
    redacted = dsn.redact_dsn("postgresql://user:secreto@postgres:5432/argentgob")
    assert "secreto" not in redacted
    assert redacted.endswith("postgres:5432/argentgob")


# ── Inicializador ────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows: list = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        self._rows = self.conn.route(sql, params)
        self.rowcount = 1

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Conexión falsa que responde a las consultas del inicializador."""

    def __init__(self, *, revision="010", policies=8, roles=None):
        self.revision = revision
        self.policies = policies
        self.roles = roles if roles is not None else {
            "argentgob_runtime": (False, False, False, True),
            "argentgob_dashboard": (False, False, False, True),
            "argentgob_app": (True, True, True, True),
        }
        self.statements: list = []
        self.autocommit = True

    def cursor(self, row_factory=None):
        return _FakeCursor(self)

    def route(self, sql, params):
        text = " ".join(str(sql).split())
        if "FROM alembic_version" in text:
            return [(self.revision,)]
        if "count(*) FROM governance_policies" in text:
            return [(self.policies,)]
        if "FROM pg_roles" in text:
            return [
                (name, *flags)
                for name, flags in self.roles.items()
                if params is None or name in params[0]
            ]
        return []

    def close(self):
        pass


def test_verify_ok_con_head_catalogo_y_roles():
    report = verify(lambda: _FakeConn())
    assert report.ok is True
    assert report.revision == "010"
    assert report.policies == 8


def test_verify_falla_sin_catalogo():
    report = verify(lambda: _FakeConn(policies=0))
    assert report.ok is False
    assert report.checks["catalogo_sembrado"] is False


def test_verify_falla_con_revision_anterior():
    report = verify(lambda: _FakeConn(revision="009"))
    assert report.checks["revision_en_head"] is False


def test_verify_falla_si_rol_es_superusuario():
    roles = {
        "argentgob_runtime": (True, False, False, True),
        "argentgob_dashboard": (False, False, False, True),
    }
    report = verify(lambda: _FakeConn(roles=roles))
    assert report.checks["rol_runtime_sin_superusuario"] is False


def test_initialize_propaga_fallo_de_postcondiciones():
    with pytest.raises(InitError):
        initialize(connection_factory=lambda: _FakeConn(policies=0), migrate=False)


def test_initialize_ok_sin_migrar(monkeypatch):
    monkeypatch.setattr(
        "argentgob.db.init.seed_policies", lambda factory: 8
    )
    monkeypatch.setattr(
        "argentgob.db.init.provision_roles", lambda factory: ["argentgob_runtime"]
    )
    report = initialize(connection_factory=lambda: _FakeConn(), migrate=False)
    assert isinstance(report, InitReport)
    assert report.ok is True
    assert report.roles_provisioned == ["argentgob_runtime"]


def test_provision_roles_sin_secreto_no_inventa_credencial(monkeypatch):
    from argentgob.db.init import provision_roles

    monkeypatch.delenv("ARGENTGOB_RUNTIME_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("ARGENTGOB_DASHBOARD_PASSWORD_FILE", raising=False)
    conn = _FakeConn()
    assert provision_roles(lambda: conn) == []
    assert not any("ALTER ROLE" in str(sql) for sql, _ in conn.statements)


def test_provision_roles_usa_archivo_de_secreto(monkeypatch, tmp_path: Path):
    from argentgob.db.init import provision_roles

    secret = tmp_path / "runtime"
    secret.write_text("p@ss", encoding="utf-8")
    monkeypatch.setenv("ARGENTGOB_RUNTIME_PASSWORD_FILE", str(secret))
    monkeypatch.delenv("ARGENTGOB_DASHBOARD_PASSWORD_FILE", raising=False)
    conn = _FakeConn()
    provisioned = provision_roles(lambda: conn)
    assert provisioned == ["argentgob_runtime"]
    assert any("ALTER ROLE" in str(sql) for sql, _ in conn.statements)


# ── Readiness ────────────────────────────────────────────────────────
def test_readiness_ok_con_esquema_y_catalogo():
    readiness = StackReadiness(connection_factory=lambda: _FakeConn())
    report = readiness.check()
    assert report.ready is True
    assert report.checks["schema_en_head"] is True
    assert report.checks["catalogo_sembrado"] is True


def test_readiness_no_ready_sin_catalogo():
    readiness = StackReadiness(connection_factory=lambda: _FakeConn(policies=0))
    assert readiness.check().ready is False


def test_readiness_no_ready_si_db_cae():
    def _boom():
        raise OSError("sin conexión")

    readiness = StackReadiness(connection_factory=_boom)
    report = readiness.check()
    assert report.ready is False
    assert report.checks["db_reachable"] is False


def test_readiness_cachea_y_permite_forzar():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _FakeConn()

    readiness = StackReadiness(connection_factory=factory, cache_seconds=60)
    readiness.check()
    readiness.check()
    assert calls["n"] == 1
    readiness.check(force=True)
    assert calls["n"] == 2


def test_readiness_informa_capacidades_sin_bloquear(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RISK_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    readiness = StackReadiness(connection_factory=lambda: _FakeConn())
    report = readiness.check()
    assert report.ready is True
    assert report.capabilities["predictor_artifacts_present"] is False
    assert report.capabilities["llm_base_configured"] is False


# ── Topología declarada (complemento del gate real) ──────────────────
def _compose() -> dict:
    import yaml

    root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_agent_es_api_persistente_sin_tty():
    agent = _compose()["services"]["agent"]
    assert agent["command"] == ["python", "-m", "argentgob.live.server"]
    assert "stdin_open" not in agent
    assert "tty" not in agent
    assert "ports" not in agent


def test_compose_agent_espera_inicializador_exitoso():
    agent = _compose()["services"]["agent"]
    assert agent["depends_on"]["migrator"]["condition"] == (
        "service_completed_successfully"
    )


def test_compose_migrator_ejecuta_inicializador_completo():
    migrator = _compose()["services"]["migrator"]
    assert migrator["command"] == ["python", "-m", "argentgob.db.init"]


def test_compose_no_interpola_contrasenas():
    text = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text(
        encoding="utf-8"
    )
    assert "POSTGRES_PASSWORD}" not in text
    assert "DATABASE_URL: \"postgresql" not in text


def test_compose_dashboard_solo_loopback_y_sin_llm():
    dashboard = _compose()["services"]["dashboard"]
    assert dashboard["ports"] == ["127.0.0.1:8501:8501"]
    assert "llama-server" not in dashboard["depends_on"]
    assert dashboard["environment"]["AUTHORIZED_AUDITOR_ROLES"] == '["auditor"]'


def test_compose_llama_pin_por_digest_y_gguf_ro():
    llama = _compose()["services"]["llama-server"]
    assert "@sha256:" in llama["image"]
    assert any(":ro" in volume for volume in llama["volumes"])


def test_compose_secretos_declarados_por_consumidor():
    services = _compose()["services"]
    assert "runtime_password" in services["agent"]["secrets"]
    assert "dashboard_password" in services["dashboard"]["secrets"]
    assert "postgres_password" in services["migrator"]["secrets"]


def test_env_example_declara_identidad_hitl():
    root = Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    assert "IDENTITY_FIXTURE_ROLES" in text
    assert "AUTHORIZED_AUDITOR_ROLES" in text
    assert json.loads('["auditor"]') == ["auditor"]
