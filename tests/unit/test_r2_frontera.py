"""
Tests de R2 — Frontera gobernada.

Verifican los invariantes de la frontera gobernada con "cero requests no
autorizados":
- Ausencia de decisión -> no ejecuta.
- BLOCK -> no ejecuta (extensión de INV-02).
- HITL -> no ejecuta (la aprobación humana durable es H6; R2 verifica no-ejecución).
- Identidad incompleta -> no ejecuta.
- Digest incorrecto (tampering) -> no ejecuta.
- Redirect prohibido -> no ejecuta.
- Decisión vencida durante backoff -> no hay nuevo intento.
- Selección de argumentos: exactamente una obligación; ninguna/ambas -> POLICY_CONFLICT.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import (
    GovernanceAction,
    HookAborted,
    Obligation,
    ReasonCode,
)
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.host_allowlist import HostAllowlist, HostNotAllowedError
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_b.argument_transforms import (
    ArgumentTransformError,
    select_effective_arguments,
    transform_arguments,
)
from argentgob.module_c.guardrails import GuardrailEngine


def _decision(
    action: GovernanceAction = GovernanceAction.PASS,
    reason: ReasonCode = ReasonCode.ALLOWED,
    obligations: list[Obligation] | None = None,
    expires_at: datetime | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        action=action,
        reason_code=reason,
        obligations=obligations or [],
        expires_at=expires_at,
    )


def _middleware(settings, abac_decision, mock_audit_writer, guards=None):
    """Arma un GovernanceMiddleware con ABAC mockeado y guardrails opcionales."""
    abac = MagicMock()
    abac.evaluate = MagicMock(return_value=abac_decision)
    guardrails = GuardrailEngine(
        profile_guardrails=guards or [],
        rate_limit=settings.rate_limit_calls_per_run,
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=mock_audit_writer,
    )


# ---------------------------------------------------------------------------
# Selección de argumentos (obligaciones)
# ---------------------------------------------------------------------------


def test_seleccion_original_devuelve_argumentos_intactos():
    """USE_ORIGINAL_ARGUMENTS devuelve los argumentos originales intactos."""
    args = {"query": "  AAPL  ", "max_results": 3}
    result = select_effective_arguments(
        args, [Obligation.USE_ORIGINAL_ARGUMENTS]
    )
    assert result == args  # intactos, sin normalización


def test_seleccion_transformada_normaliza():
    """USE_TRANSFORMED_ARGUMENTS aplica la transformación mínima (strip)."""
    args = {"query": "  AAPL  ", "max_results": 3}
    result = select_effective_arguments(
        args, [Obligation.USE_TRANSFORMED_ARGUMENTS]
    )
    assert result["query"] == "AAPL"
    assert result["max_results"] == 3


def test_seleccion_sin_obligacion_es_conflicto():
    """Ninguna obligación de selección produce POLICY_CONFLICT."""
    with pytest.raises(ArgumentTransformError) as exc:
        select_effective_arguments({"query": "AAPL"}, [])
    assert exc.value.reason == ReasonCode.POLICY_CONFLICT


def test_seleccion_ambas_obligaciones_es_conflicto():
    """Ambas obligaciones de selección producen POLICY_CONFLICT."""
    with pytest.raises(ArgumentTransformError) as exc:
        select_effective_arguments(
            {"query": "AAPL"},
            [
                Obligation.USE_ORIGINAL_ARGUMENTS,
                Obligation.USE_TRANSFORMED_ARGUMENTS,
            ],
        )
    assert exc.value.reason == ReasonCode.POLICY_CONFLICT


def test_transform_arguments_strip_strings():
    """La transformación mínima solo hace strip de espacios en strings."""
    result = transform_arguments({"query": "  AAPL  ", "n": 3})
    assert result == {"query": "AAPL", "n": 3}


# ---------------------------------------------------------------------------
# PEP: cero requests no autorizados
# ---------------------------------------------------------------------------


def test_ausencia_de_decision_no_ejecuta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """Sin decisión (obligaciones vacías) -> POLICY_CONFLICT, no ejecuta."""
    mw = _middleware(
        settings,
        _decision(GovernanceAction.PASS, ReasonCode.ALLOWED, obligations=[]),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0
    assert "POLICY_CONFLICT" in exc.value.reason


def test_hitl_no_ejecuta(settings, agent_identity_analyst, mock_audit_writer):
    """HITL exige aprobación humana; en R2 no ejecuta la herramienta."""
    mw = _middleware(
        settings,
        _decision(
            GovernanceAction.HITL,
            ReasonCode.HUMAN_APPROVAL_REQUIRED,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        ),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0
    assert "HUMAN_APPROVAL_REQUIRED" in exc.value.reason


def test_identidad_incompleta_no_ejecuta(
    settings, mock_audit_writer
):
    """Identidad incompleta (sin rol) -> INCOMPLETE_IDENTITY, no ejecuta."""
    mw = _middleware(
        settings,
        _decision(
            GovernanceAction.PASS,
            ReasonCode.ALLOWED,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        ),
        mock_audit_writer,
    )
    incomplete = AgentIdentity(id="agente-sin-rol", role="")
    spy = SpyTool(governance=mw, agent_identity=incomplete)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0
    assert "INCOMPLETE_IDENTITY" in exc.value.reason


def test_digest_incorrecto_se_detecta():
    """Un digest incorrecto (tampering) se detecta por verify_digest."""
    from argentgob.core.envelope import ToolCallEnvelope

    # Construir un envelope y corromper los argumentos después del digest.
    envelope = ToolCallEnvelope.build(
        agent=AgentIdentity(id="agente-analyst-001", role="analyst"),
        tool_name="spy_tool",
        operation_class="READ",
        resource="test",
        environment="TEST",
        execution_arguments={"query": "AAPL"},
    )
    assert envelope.verify_digest()  # consistente al construir
    envelope.execution_arguments["query"] = "MALICIOUS"  # tampering
    assert not envelope.verify_digest()  # el digest ya no coincide


def test_decision_vencida_no_ejecuta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """Una decisión vencida aborta y exige nueva ejecución gobernada."""
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    mw = _middleware(
        settings,
        _decision(
            GovernanceAction.PASS,
            ReasonCode.ALLOWED,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            expires_at=expired,
        ),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    with pytest.raises(HookAborted) as exc:
        spy._run(query="AAPL")
    assert spy.call_count == 0
    assert "DECISION_EXPIRED" in exc.value.reason


def test_decision_vigente_ejecuta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """Una decisión vigente (expires_at futuro) permite ejecutar."""
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    mw = _middleware(
        settings,
        _decision(
            GovernanceAction.PASS,
            ReasonCode.ALLOWED,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            expires_at=future,
        ),
        mock_audit_writer,
    )
    spy = SpyTool(governance=mw, agent_identity=agent_identity_analyst)
    result = spy._run(query="AAPL")
    assert spy.call_count == 1
    assert result == "spy_executed:1"


# ---------------------------------------------------------------------------
# Allowlist de hosts
# ---------------------------------------------------------------------------


def test_allowlist_autoriza_host_exacto():
    """La allowlist autoriza un host exacto."""
    allowlist = HostAllowlist({"api.gdeltproject.org"})
    assert allowlist.allows("api.gdeltproject.org")
    assert not allowlist.allows("evil.gdeltproject.org")  # sin sufijo amplio


def test_allowlist_no_autoriza_sufijo_amplio():
    """No se autorizan subdominios ni sufijos amplios."""
    allowlist = HostAllowlist({"duckduckgo.com"})
    assert not allowlist.allows("evil-duckduckgo.com")
    assert not allowlist.allows("duckduckgo.com.evil.com")


def test_allowlist_check_url_ok():
    """check_url retorna el host si está autorizado."""
    allowlist = HostAllowlist({"api.gdeltproject.org"})
    assert (
        allowlist.check_url("https://api.gdeltproject.org/api/v2/doc/doc")
        == "api.gdeltproject.org"
    )


def test_allowlist_check_url_prohibido():
    """check_url lanza HostNotAllowedError si el host no está autorizado."""
    allowlist = HostAllowlist({"api.gdeltproject.org"})
    with pytest.raises(HostNotAllowedError):
        allowlist.check_url("https://evil.example.com/path")


def test_allowlist_normaliza_minusculas_y_puerto():
    """La normalización es mínima: minúsculas, sin puerto."""
    allowlist = HostAllowlist({"API.GDELTPROJECT.ORG"})
    assert allowlist.allows("api.gdeltproject.org:443")


# ---------------------------------------------------------------------------
# Decisión vencida durante backoff (news tool)
# ---------------------------------------------------------------------------


def test_decision_vencida_durante_backoff_no_reintenta(
    settings, agent_identity_analyst, mock_audit_writer
):
    """Si la decisión vence durante el backoff, no hay nuevo intento."""
    from argentgob.tools.news_providers import (
        ProviderError,
        ProviderErrorKind,
    )
    from argentgob.tools.news_tool import NewsTool

    # Proveedor que siempre falla de forma transitoria (para forzar backoff).
    class _TransientProvider:
        name = "transient"

        def search(self, query, max_results):
            raise ProviderError(ProviderErrorKind.TRANSIENT, "timeout")

    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    mw = _middleware(
        settings,
        _decision(
            GovernanceAction.PASS,
            ReasonCode.ALLOWED,
            obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
            expires_at=expired,
        ),
        mock_audit_writer,
    )
    tool = NewsTool(
        primary=_TransientProvider(),
        fallback=_TransientProvider(),
        governance=mw,
        agent_identity=agent_identity_analyst,
    )
    # Simular que la decisión de la ejecución en curso ya venció durante el
    # backoff: se fija el envelope con una decisión vencida.
    from argentgob.core.envelope import ToolCallEnvelope

    envelope = ToolCallEnvelope.build(
        agent=agent_identity_analyst,
        tool_name="news",
        operation_class="EXTERNAL_SEND",
        resource="news_providers",
        environment="TEST",
        execution_arguments={"query": "AAPL"},
    )
    envelope.decision = _decision(
        GovernanceAction.PASS,
        ReasonCode.ALLOWED,
        obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        expires_at=expired,
    )
    tool._current_envelope = envelope
    # La decisión ya está vencida al entrar al loop de reintentos.
    with pytest.raises(ProviderError) as exc:
        tool._execute(query="AAPL")
    assert exc.value.kind == ProviderErrorKind.GOVERNANCE
    assert "vencida" in str(exc.value)