"""
Fixtures compartidas para la suite de tests.

Los tests unitarios NO requieren una base de datos real: el AuditWriter se
reemplaza por un mock y el ABACEvaluator usa las políticas in-memory del perfil
(fallback seguro cuando no hay PostgreSQL disponible).
"""
from unittest.mock import MagicMock

import pytest

from argentgob.core.config import Settings
from argentgob.core.envelope import AgentIdentity
from argentgob.module_c.profiles import PROFILES


@pytest.fixture
def settings() -> Settings:
    """Settings de prueba con valores determinísticos."""
    return Settings(
        environment="TEST",
        log_level="WARNING",
        max_payload_bytes=65536,
        max_string_bytes=4096,
        rate_limit_calls_per_run=5,
    )


@pytest.fixture
def agent_identity_analyst() -> AgentIdentity:
    """Identidad de un agente con el perfil 'analyst'."""
    return AgentIdentity(id="agente-analyst-001", role="analyst")


@pytest.fixture
def agent_identity_restricted() -> AgentIdentity:
    """Identidad de un agente con el perfil 'analyst_restricted'."""
    return AgentIdentity(id="agente-restricted-001", role="analyst_restricted")


@pytest.fixture
def profile_analyst():
    """Perfil 'analyst' completo."""
    return PROFILES["analyst"]


@pytest.fixture
def profile_restricted():
    """Perfil 'analyst_restricted' completo."""
    return PROFILES["analyst_restricted"]


@pytest.fixture
def mock_audit_writer() -> MagicMock:
    """AuditWriter simulado: registra llamadas sin tocar la base de datos."""
    audit = MagicMock()
    audit.record_decision = MagicMock(return_value=None)
    audit.record_result = MagicMock(return_value=None)
    return audit
