"""CrewAI real en la ruta publica de la tool (sesgo 5 de docs/analytics/0004).

Los tests previos de integracion usaban `SpyTool` (herramienta de prueba).
Este modulo valida que la herramienta CrewAI real (`NewsTool`) ejecuta su
`_execute` a traves de la ruta publica `BaseTool.run()` (la que CrewAI usa en
produccion), con gobernanza activa, y que los providers no se consultan cuando
la decision es BLOCK (INV-02: cero requests no autorizados).
"""
from __future__ import annotations

import pytest

from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import DEFAULT_MODEL_DIR, RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import HookAborted
from argentgob.db.audit import AuditWriter
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.module_c.profiles import PROFILES
from argentgob.tools.news_providers import NewsItem
from argentgob.tools.news_tool import NewsTool
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


class _FakeProvider:
    """Proveedor de noticias configurable (sin red)."""

    def __init__(self, name: str, items: list[NewsItem] | None = None):
        self.name = name
        self.calls = 0
        self._items = items or []

    def search(self, query: str, max_results: int) -> list[NewsItem]:
        self.calls += 1
        return self._items


def _news_tool_with_governance(
    settings, db: _MemDb, monkeypatch, *, profile_name: str = "analyst",
    provider_items: list[NewsItem] | None = None,
) -> tuple[NewsTool, _FakeProvider]:
    """NewsTool real con middleware de gobernanza y provider fake."""
    from argentgob.db import connection as connection_module

    monkeypatch.setattr(connection_module, "get_connection", db.factory)

    profile = PROFILES[profile_name]
    abac = ABACEvaluator(settings, profile)
    monkeypatch.setattr(
        abac, "_load_allowed_tools", lambda: list(profile.allowed_tools)
    )
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
    primary = _FakeProvider("gdelt", provider_items or [])
    fallback = _FakeProvider("duckduckgo", provider_items or [])
    tool = NewsTool(
        governance=mw,
        agent_identity=AgentIdentity(id="agente-analyst-crewai", role=profile_name),
        primary=primary,
        fallback=fallback,
    )
    return tool, primary


@requires_model
def test_news_tool_real_ejecuta_via_run_con_provider_fake(settings, monkeypatch):
    """Sesgo 5: NewsTool real via BaseTool.run() con gobernanza PASS ejecuta.

    El provider fake se consulta una vez (primario) y la herramienta devuelve
    el formato de noticias; la decision queda PASS en la auditoria.
    """
    db = _MemDb()
    tool, primary = _news_tool_with_governance(
        settings, db, monkeypatch,
        provider_items=[NewsItem(title="Bitcoin sube", url="https://x.com/1", source="gdelt")],
    )

    result = tool.run(query="bitcoin mercado")

    assert primary.calls == 1
    assert "Bitcoin sube" in result
    # La decision PASS quedo persistida.
    decisions = list(db.store["governance_decisions"].values())
    assert len(decisions) == 1
    assert decisions[0]["action"] == "PASS"
    assert decisions[0]["tool_name"] == "news"
    # INV-05: el resultado se registro como evento SUCCESS.
    events = list(db.store["governance_events"].values())
    assert len(events) == 1
    assert events[0]["status"] == "SUCCESS"


@requires_model
def test_news_tool_real_bloquea_prediccion_high_sin_provider(settings, monkeypatch):
    """Sesgo 5: BLOCK por prediccion HIGH no consulta el provider (INV-02)."""
    db = _MemDb()
    tool, primary = _news_tool_with_governance(
        settings, db, monkeypatch,
        provider_items=[NewsItem(title="X", url="https://x.com/1", source="gdelt")],
    )
    # Forzar prediccion HIGH (pasa el umbral).
    from argentgob.analytics.predictor import RiskPrediction

    real_predict = tool.governance.risk_predictor.predict

    def forced_high(features):
        pred = real_predict(features)
        if pred is None:
            return None
        return RiskPrediction(
            predicted_risk_class="HIGH",
            risk_probability=0.99,
            class_probabilities={"LOW": 0.0, "MEDIUM": 0.01, "HIGH": 0.99},
            model_id=pred.model_id,
            model_version=pred.model_version,
            feature_schema_version=pred.feature_schema_version,
            inference_latency_ms=pred.inference_latency_ms,
        )

    tool.governance.risk_predictor.predict = forced_high

    with pytest.raises(HookAborted) as exc:
        tool.run(query="bitcoin mercado")
    assert "PREDICTED_HIGH_RISK" in exc.value.reason
    assert primary.calls == 0  # INV-02: la tool nunca consulto el provider
    # La decision BLOCK quedo persistida.
    decisions = list(db.store["governance_decisions"].values())
    assert len(decisions) == 1
    assert decisions[0]["action"] == "BLOCK"
    assert decisions[0]["reason_code"] == "PREDICTED_HIGH_RISK"
    # Sin evento (no hubo ejecucion).
    assert len(db.store["governance_events"]) == 0


@requires_model
def test_news_tool_real_bloquea_guardrail_sin_provider(settings, monkeypatch):
    """Sesgo 5: BLOCK por guardrail (topic_relevance) no consulta el provider."""
    db = _MemDb()
    tool, primary = _news_tool_with_governance(
        settings, db, monkeypatch,
        provider_items=[NewsItem(title="X", url="https://x.com/1", source="gdelt")],
    )

    # Query no financiera: el guardrail topic_relevance la bloquea.
    with pytest.raises(HookAborted) as exc:
        tool.run(query="que hora es")
    assert "TOPIC_NOT_FINANCIAL" in exc.value.reason
    assert primary.calls == 0
    decisions = list(db.store["governance_decisions"].values())
    assert decisions[0]["action"] == "BLOCK"
    assert decisions[0]["reason_code"] == "TOPIC_NOT_FINANCIAL"