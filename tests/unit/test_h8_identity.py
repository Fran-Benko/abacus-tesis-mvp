"""Tests de H8 — Unidad 2: identidad y autorización de la consola.

Cubren:
- `AppRole` resuelve roles por valor y rechaza desconocidos.
- `AuthenticationEvidence` expira y serializa solo campos autorizados.
- `AuditorIdentityProvider.authenticate` genera evidencia fixture con
  assurance DEVELOPMENT y development_only=True.
- Modo fixture bloqueado en producción (no es autenticación fuerte).
- `has_role`/`require_role` exigen evidencia vigente + rol.
"""
from datetime import datetime, timedelta, timezone

import pytest

from argentgob.identity.evidence import AuthenticationEvidence
from argentgob.identity.provider import (
    AuditorIdentityProvider,
    ExpiredEvidenceError,
    FixtureNotAllowedInProductionError,
    UnauthenticatedError,
    UnauthorizedRoleError,
)
from argentgob.identity.roles import AppRole


def _provider(**over):
    base = {
        "environment": "TEST",
        "development_only": True,
        "session_ttl_seconds": 3600,
        "fixture_roles": {"auditor-1": ["viewer", "auditor"]},
    }
    base.update(over)
    return AuditorIdentityProvider(**base)


def test_app_role_from_value():
    assert AppRole.from_value("viewer") is AppRole.VIEWER
    assert AppRole.from_value("auditor") is AppRole.AUDITOR
    assert AppRole.from_value("policy_admin") is AppRole.POLICY_ADMIN
    assert AppRole.from_value("operator") is AppRole.OPERATOR


def test_app_role_rechaza_desconocido():
    with pytest.raises(ValueError):
        AppRole.from_value("superuser")


def test_evidence_expira():
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    ev = AuthenticationEvidence(
        subject="a",
        provider="fixture",
        method="fixture",
        assurance="DEVELOPMENT",
        identifier="a",
        expires_at=past,
    )
    assert ev.is_expired() is True


def test_evidence_no_expira_sin_expires_at():
    ev = AuthenticationEvidence(
        subject="a",
        provider="fixture",
        method="fixture",
        assurance="DEVELOPMENT",
        identifier="a",
        expires_at=None,
    )
    assert ev.is_expired() is False


def test_evidence_to_dict_no_expone_credenciales():
    ev = AuthenticationEvidence(
        subject="auditor-1",
        provider="fixture",
        method="fixture",
        assurance="DEVELOPMENT",
        identifier="auditor-1",
    )
    d = ev.to_dict()
    assert d["subject"] == "auditor-1"
    assert d["assurance"] == "DEVELOPMENT"
    # Nunca credenciales/tokens.
    assert "token" not in d
    assert "password" not in d
    assert "secret" not in d


def test_authenticate_fixture_genera_evidencia_development():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    assert ev.development_only is True
    assert ev.assurance == "DEVELOPMENT"
    assert ev.provider == "fixture"
    assert ev.identifier == "auditor-1"
    assert ev.expires_at is not None


def test_authenticate_acepta_app_role():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role=AppRole.AUDITOR)
    assert ev.identifier == "auditor-1"


def test_authenticate_bloqueado_en_produccion():
    provider = _provider(environment="PROD")
    with pytest.raises(FixtureNotAllowedInProductionError):
        provider.authenticate(identifier="auditor-1", role="auditor")


def test_authenticate_bloqueado_si_no_development_only():
    provider = _provider(development_only=False)
    with pytest.raises(FixtureNotAllowedInProductionError):
        provider.authenticate(identifier="auditor-1", role="auditor")


def test_has_role_true_con_evidencia_vigente():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    assert provider.has_role(ev, "auditor") is True
    assert provider.has_role(ev, "viewer") is True
    assert provider.has_role(ev, "operator") is False


def test_has_role_false_sin_evidencia():
    provider = _provider()
    assert provider.has_role(None, "auditor") is False


def test_has_role_false_con_evidencia_vencida():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    ev.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert provider.has_role(ev, "auditor") is False


def test_require_role_ok():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    assert provider.require_role(ev, "auditor") is ev


def test_require_role_sin_evidencia():
    provider = _provider()
    with pytest.raises(UnauthenticatedError):
        provider.require_role(None, "auditor")


def test_require_role_evidencia_vencida():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    ev.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ExpiredEvidenceError):
        provider.require_role(ev, "auditor")


def test_require_role_rol_no_autorizado():
    provider = _provider()
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    with pytest.raises(UnauthorizedRoleError):
        provider.require_role(ev, "operator")


def test_authentication_evidence_serializa_o_none():
    provider = _provider()
    assert provider.authentication_evidence(None) is None
    ev = provider.authenticate(identifier="auditor-1", role="auditor")
    d = provider.authentication_evidence(ev)
    assert d["identifier"] == "auditor-1"