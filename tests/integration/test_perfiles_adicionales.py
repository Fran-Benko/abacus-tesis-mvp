"""Cobertura de perfiles adicionales (sesgo 4 de docs/analytics/0004).

Hasta ahora los tests de integracion solo cubrian `analyst` y
`analyst_restricted`. Este modulo agrega cobertura para:

1. Perfil `admin`: sin guardrails activos y con `max_tool_calls=20`. Valida
   que una query que en `analyst` seria bloqueada por el guardrail
   `topic_relevance` (TOPIC_NOT_FINANCIAL) se ejecuta en `admin` porque el
   perfil no activa ese guardrail, mientras la prediccion se mantiene LOW.
2. Un perfil con transformacion de argumentos (`USE_TRANSFORMED_ARGUMENTS`)
   via `DeterministicEvaluator` end-to-end: la herramienta recibe los
   argumentos efectivos normalizados (strip), nunca una preview, y la
   decision persiste las obligaciones efectivas.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import DEFAULT_MODEL_DIR, RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import GovernanceAction, HookAborted, Obligation
from argentgob.db.audit import AuditWriter
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.deterministic_evaluator import DeterministicEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.module_c.policy import GovernancePolicy
from argentgob.module_c.policy_store import PolicyStore
from argentgob.module_c.profiles import PROFILES
from tests.integration._mem_db import MemConn, _new_store

MODEL_EXISTS = (DEFAULT_MODEL_DIR / "pipeline.joblib").exists()

requires_model = pytest.mark.skipif(
    not MODEL_EXISTS,
    reason="modelo versionado no presente",
)


class _MemDb:
    """Fabrica de conexiones en memoria: comparte el mismo store."""

    def __init__(self):
        self.store = _new_store()

    def factory(self):
        return MemConn(self.store)


class _FlowSpyTool(SpyTool):
    """SpyTool con nombre configurable para simular herramientas del catalogo."""

    def __init__(self, tool_name: str, **kwargs):
        super().__init__(**kwargs)
        self.name = tool_name


def _admin_middleware(settings, db: _MemDb, monkeypatch):
    """Middleware real para el perfil admin (sin guardrails, max 20 calls)."""
    from argentgob.db import connection as connection_module

    monkeypatch.setattr(connection_module, "get_connection", db.factory)

    profile = PROFILES["admin"]
    assert profile.active_guardrails == []  # contrato del perfil
    assert profile.max_tool_calls == 20
    abac = ABACEvaluator(settings, profile)
    monkeypatch.setattr(
        abac, "_load_allowed_tools", lambda: list(profile.allowed_tools)
    )
    guardrails = GuardrailEngine(
        profile_guardrails=profile.active_guardrails,
        rate_limit=settings.rate_limit_calls_per_run,
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=AuditWriter(settings),
        feature_builder=FeatureBuilder(),
        risk_predictor=RiskPredictor(),
        prediction_store=PredictionStore(settings),
    )


@requires_model
def test_perfil_admin_sin_guardrails_ejecuta_query_no_financiera(
    settings, monkeypatch
):
    """Sesgo 4: una query no financiera bloqueada en analyst se ejecuta en admin.

    En `analyst` el guardrail `topic_relevance` bloquea búsquedas sin términos
    financieros (TOPIC_NOT_FINANCIAL). El perfil `admin` no activa ese
    guardrail, por lo que la misma query es ejecutable (prediccion LOW).
    """
    db = _MemDb()
    mw = _admin_middleware(settings, db, monkeypatch)
    identity = AgentIdentity(id="agente-admin-001", role="admin")

    tool = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"

    result = tool._run(query="que hora es")

    # Sin guardrail topic_relevance, la query se ejecuta (prediccion LOW).
    assert result.startswith("spy_executed")
    assert tool.call_count == 1

    # La decision quedo persistida como PASS con ALLOWED.
    decisions = list(db.store["governance_decisions"].values())
    assert len(decisions) == 1
    assert decisions[0]["action"] == "PASS"
    assert decisions[0]["agent_role"] == "admin"


@requires_model
def test_perfil_admin_no_eleva_a_hitl_con_prediccion_low(settings, monkeypatch):
    """Sesgo 4: en admin una prediccion LOW no eleva la decision a HITL."""
    db = _MemDb()
    mw = _admin_middleware(settings, db, monkeypatch)
    identity = AgentIdentity(id="agente-admin-002", role="admin")

    tool = _FlowSpyTool("stock_price", governance=mw, agent_identity=identity)
    tool.operation_class = "READ"
    tool.resource = "yahoo_finance"

    result = tool._run(ticker="AAPL")
    assert result.startswith("spy_executed")

    decisions = list(db.store["governance_decisions"].values())
    assert decisions[0]["action"] == "PASS"

    # Prediccion LOW registrada (sin HITL originado por prediccion).
    preds = list(db.store["risk_predictions"].values())
    assert all(p["predicted_risk_class"] == "LOW" for p in preds)


# -- Transformacion de argumentos (política USE_TRANSFORMED_ARGUMENTS) --------

def _policy_store_with(policies: list[GovernancePolicy]) -> PolicyStore:
    """PolicyStore in-memory que sirve las políticas dadas a traves de load_current."""
    store = PolicyStore.__new__(PolicyStore)
    store.settings = None
    store.ttl_seconds = 300
    store._cache = None
    store._lock = __import__("threading").Lock()
    store._db_available = True
    store._policies = policies
    store.load_current = lambda: list(store._policies)
    return store


def _effective_policy(
    *,
    tool_name: str = "news",
    operation_class: str = "EXTERNAL_SEND",
    resource: str = "duckduckgo.com",
    obligations: list[Obligation],
    transform_spec: dict | None = None,
    profile_name: str = "analyst",
) -> GovernancePolicy:
    """Politica PASS vigente para el envelope dado."""
    return GovernancePolicy(
        policy_id=f"POL-{tool_name}-eff",
        policy_version=1,
        profile_name=profile_name,
        tool_name=tool_name,
        operation_class=operation_class,
        environment="TEST",
        resource=resource,
        effect=GovernanceAction.PASS,
        priority=50,
        obligations=obligations,
        transform_spec=transform_spec,
        valid_from=datetime.now(UTC) - timedelta(days=1),
        valid_until=None,
        policy_digest="sha256:test",
    )


def _build_transformed_middleware(
    settings, db: _MemDb, monkeypatch, *, obligations, transform_spec=None
) -> GovernanceMiddleware:
    """Middleware real usando DeterministicEvaluator con una politica dada."""
    from argentgob.db import connection as connection_module

    monkeypatch.setattr(connection_module, "get_connection", db.factory)

    store = _policy_store_with(
        [
            _effective_policy(
                tool_name="news",
                operation_class="EXTERNAL_SEND",
                resource="duckduckgo.com",
                obligations=obligations,
                transform_spec=transform_spec,
            )
        ]
    )
    evaluator = DeterministicEvaluator(settings=settings, policy_source=store)
    guardrails = GuardrailEngine(
        profile_guardrails=[],  # sin guardrails para aislar la transformacion
        rate_limit=settings.rate_limit_calls_per_run,
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=evaluator,
        guardrails=guardrails,
        audit=AuditWriter(settings),
        feature_builder=FeatureBuilder(),
        risk_predictor=RiskPredictor(),
        prediction_store=PredictionStore(settings),
    )


class _ReceivedArgsTool(_FlowSpyTool):
    """SpyTool que captura los argumentos efectivos recibidos."""

    received: dict | None = None

    def _execute(self, **kwargs):
        _ReceivedArgsTool.received = dict(kwargs)
        return super()._execute(**kwargs)


@requires_model
def test_use_transformed_arguments_entrega_normalizados(settings, monkeypatch):
    """Sesgo 4: USE_TRANSFORMED_ARGUMENTS entrega args efectivos normalizados.

    La herramienta recibe el objeto transformado (strip de espacios), nunca la
    preview; el digest de la decision corresponde a los argumentos originales.
    """
    db = _MemDb()
    _ReceivedArgsTool.received = None
    mw = _build_transformed_middleware(
        settings,
        db,
        monkeypatch,
        obligations=[Obligation.USE_TRANSFORMED_ARGUMENTS],
        transform_spec={"fields": ["query"]},
    )
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")

    tool = _ReceivedArgsTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"

    result = tool._run(query="  bitcoin  ")
    assert result.startswith("spy_executed")
    assert tool.call_count == 1

    # La herramienta recibio los argumentos efectivos normalizados (strip).
    assert _ReceivedArgsTool.received == {"query": "bitcoin"}

    # La decision persistida conserva las obligaciones efectivas.
    decisions = list(db.store["governance_decisions"].values())
    assert len(decisions) == 1
    # El digest corresponde a los argumentos ORIGINALES (no transformados),
    # consistente con el envelope que entro al PEP.
    from argentgob.core.envelope import _canonical_digest

    assert decisions[0]["payload_digest"] == _canonical_digest(
        {"query": "  bitcoin  "}
    )


@requires_model
def test_use_original_arguments_entrega_intactos(settings, monkeypatch):
    """Sesgo 4: USE_ORIGINAL_ARGUMENTS entrega los argumentos intactos."""
    db = _MemDb()
    _ReceivedArgsTool.received = None
    mw = _build_transformed_middleware(
        settings,
        db,
        monkeypatch,
        obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
    )
    identity = AgentIdentity(id="agente-analyst-002", role="analyst")

    tool = _ReceivedArgsTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"

    result = tool._run(query="  bitcoin  ")
    assert result.startswith("spy_executed")

    # USE_ORIGINAL: la tool recibe exactamente los argumentos de entrada.
    assert _ReceivedArgsTool.received == {"query": "  bitcoin  "}


@requires_model
def test_obligaciones_incompatibles_bloquean_con_policy_conflict(
    settings, monkeypatch
):
    """Sesgo 4: USE_ORIGINAL + USE_TRANSFORMED juntas producen POLICY_CONFLICT.

    El DeterministicEvaluator detecta obligaciones incompatibles para el
    mismo alcance y la decision es BLOCK (cero ejecucion).
    """
    db = _MemDb()
    mw = _build_transformed_middleware(
        settings,
        db,
        monkeypatch,
        obligations=[
            Obligation.USE_ORIGINAL_ARGUMENTS,
            Obligation.USE_TRANSFORMED_ARGUMENTS,
        ],
    )
    identity = AgentIdentity(id="agente-analyst-003", role="analyst")

    tool = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"

    with pytest.raises(HookAborted) as exc:
        tool._run(query="bitcoin")
    assert "POLICY_CONFLICT" in exc.value.reason
    assert tool.call_count == 0  # INV-02: cero ejecucion
    # La decision quedo persistida como BLOCK por conflicto.
    decisions = list(db.store["governance_decisions"].values())
    assert decisions[0]["action"] == "BLOCK"
    assert decisions[0]["reason_code"] == "POLICY_CONFLICT"