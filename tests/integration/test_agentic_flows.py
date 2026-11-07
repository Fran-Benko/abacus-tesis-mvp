"""
Tests de integracion de flujos agencios reales.

Reproducen un usuario ejecutando analisis agencios con agentes de IA: un
agente `analyst` recorre un analisis de mercado con varias tool calls
(benignas, con inyeccion SQL, con PII y una herramienta no permitida). El
objetivo es validar que la solucion **valide, analice, prediga y/o bloquee**
lo que corresponde en cada paso y que ese resultado quede **reflejado en los
servicios de consulta del dashboard** (ConsoleQueryService y
PredictionQueryService) y en la **auditoria persistente**.

A diferencia de los tests unitarios, aqui se usan los componentes reales:
- `RiskPredictor` real (modelo versionado `risk_model_v1`) y `FeatureBuilder`
  real para la inferencia predictiva pre-ejecucion.
- `ABACEvaluator` y `GuardrailEngine` reales (politicas del perfil).
- `AuditWriter` y `PredictionStore` reales, con la conexion a PostgreSQL
  reemplazada por una conexion en memoria que implementa las tablas/vistas de
  gobernanza (`governance_decisions`, `governance_events`,
  `risk_predictions`, `v_governance_console`, `v_risk_evidence`).

De este modo se verifica el flujo completo: interceptar -> sanitizar -> predecir
-> decidir -> persistir -> consultar (dashboard) -> auditar.

Si el modelo versionado no existe, los tests que dependen de la inferencia
real se omiten (misma politica que test_analytics_integration.py).
"""
from __future__ import annotations

import pytest

from argentgob.analytics.evidence import PredictionQueryService
from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import DEFAULT_MODEL_DIR, RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.console.service import ConsoleQueryService
from argentgob.core.config import Settings
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import HookAborted
from argentgob.db.audit import AuditWriter
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.module_c.profiles import PROFILES

MODEL_EXISTS = (DEFAULT_MODEL_DIR / "pipeline.joblib").exists()

requires_model = pytest.mark.skipif(
    not MODEL_EXISTS,
    reason="modelo versionado no presente",
)


class _FlowSpyTool(SpyTool):
    """SpyTool con nombre configurable para simular herramientas del catalogo."""

    def __init__(self, tool_name: str, **kwargs):
        super().__init__(**kwargs)
        self.name = tool_name


# -- Conexion en memoria que implementa las tablas/vistas de gobernanza --

class _MemCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows: list[dict] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.record.append((sql, params))
        rowcount, rows = self.conn.route(sql, params)
        self.rowcount = rowcount
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MemConn:
    """Conexion falsa que persiste decisiones, eventos y predicciones."""

    def __init__(self, store):
        self.autocommit = True
        self.store = store
        self.record: list[tuple[str, dict]] = []
        self.closed = False

    def cursor(self, row_factory=None):
        return _MemCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def route(self, sql, params=None):
        params = params or {}
        if "INSERT INTO governance_decisions" in sql:
            self.store["decisions"][params["decision_id"]] = {
                "decision_id": params["decision_id"],
                "event_id": params["event_id"],
                "agent_id": params["agent_id"],
                "agent_role": params["agent_role"],
                "tool_name": params["tool_name"],
                "operation_class": params["operation_class"],
                "action": params["action"],
                "reason_code": params["reason_code"],
                "policy_id": params["policy_id"],
                "payload_digest": params["payload_digest"],
                "blocked_by_guardrail": params.get("blocked_by"),
                "latency_pre_ms": params.get("latency_pre_ms"),
                "decided_at": params["decided_at"],
            }
            return (1, [])
        if "INSERT INTO governance_events" in sql:
            self.store["events"][params["event_id"]] = {
                "event_id": params["event_id"],
                "decision_id": params.get("decision_id") or "",
                "tool_name": params["tool_name"],
                "status": params["status"],
                "error_message": params.get("error_message"),
                "latency_post_ms": params.get("latency_post_ms"),
            }
            return (1, [])
        if "INSERT INTO risk_predictions" in sql:
            self.store["predictions"][params["prediction_id"]] = {
                "prediction_id": params["prediction_id"],
                "event_id": params["event_id"],
                "decision_id": params.get("decision_id"),
                "model_id": params["model_id"],
                "model_version": params["model_version"],
                "predicted_risk_class": params["predicted_risk_class"],
                "risk_probability": params["risk_probability"],
                "class_probabilities": params["class_probabilities"],
                "inference_latency_ms": params["inference_latency_ms"],
                "feature_schema_version": params["feature_schema_version"],
            }
            return (1, [])
        if "FROM v_governance_console" in sql:
            return (0, self._v_governance_console(sql, params))
        if "FROM v_risk_evidence" in sql:
            return (0, self._v_risk_evidence(sql, params))
        if "FROM approval_requests" in sql:
            return (0, [])
        return (0, [])

    # -- v_governance_console (decisiones + eventos) --
    def _console_rows(self) -> list[dict]:
        rows = []
        for d in self.store["decisions"].values():
            ev = self.store["events"].get(d["event_id"])
            rows.append(
                {
                    "decision_id": d["decision_id"],
                    "event_id": d["event_id"],
                    "agent_id": d["agent_id"],
                    "agent_role": d["agent_role"],
                    "tool_name": d["tool_name"],
                    "operation_class": d["operation_class"],
                    "resource": None,
                    "environment": None,
                    "payload_digest": d["payload_digest"],
                    "action": d["action"],
                    "risk_level": "LOW",
                    "reason_codes": [d["reason_code"]],
                    "policy_id": d["policy_id"],
                    "policy_version": None,
                    "effective_priority": None,
                    "conflicts": [],
                    "obligations": [],
                    "decided_at": d["decided_at"],
                    "expires_at": None,
                    "status": ev["status"] if ev else None,
                    "error_digest": None,
                    "result_digest": None,
                    "latency_post_ms": ev["latency_post_ms"] if ev else None,
                    "recorded_at": None,
                }
            )
        return rows

    def _v_governance_console(self, sql, params) -> list[dict]:
        rows = self._console_rows()
        if "COUNT(*) FILTER" in sql:
            total = len(rows)
            return [
                {
                    "total_pass": sum(1 for r in rows if r["action"] == "PASS"),
                    "total_block": sum(1 for r in rows if r["action"] == "BLOCK"),
                    "total_hitl": sum(1 for r in rows if r["action"] == "HITL"),
                    "total": total,
                    "total_failed": sum(1 for r in rows if r["status"] == "FAILED"),
                    "total_unknown": sum(1 for r in rows if r["status"] == "UNKNOWN"),
                    "total_no_result": sum(1 for r in rows if r["status"] is None),
                }
            ]
        if "percentile_cont" in sql:
            lat = sorted(
                r["latency_post_ms"]
                for r in rows
                if r["latency_post_ms"] is not None
            )
            if not lat:
                return [{"p50": None, "p90": None, "p99": None}]

            def pct(p):
                return lat[int(p * (len(lat) - 1))]

            return [{"p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99)}]
        if "ORDER BY decided_at DESC" in sql:
            if params.get("action"):
                rows = [r for r in rows if r["action"] == params["action"]]
            return rows
        return rows

    # -- v_risk_evidence (predicciones + decision final) --
    def _evidence_rows(self) -> list[dict]:
        rows = []
        for p in self.store["predictions"].values():
            d = (
                self.store["decisions"].get(p["decision_id"])
                if p["decision_id"]
                else None
            )
            rows.append(
                {
                    "prediction_id": p["prediction_id"],
                    "event_id": p["event_id"],
                    "decision_id": p["decision_id"],
                    "model_id": p["model_id"],
                    "model_version": p["model_version"],
                    "predicted_risk_class": p["predicted_risk_class"],
                    "risk_probability": p["risk_probability"],
                    "class_probabilities": p["class_probabilities"],
                    "inference_latency_ms": p["inference_latency_ms"],
                    "feature_schema_version": p["feature_schema_version"],
                    "predicted_at": None,
                    "final_action": d["action"] if d else None,
                    "final_reason_code": d["reason_code"] if d else None,
                    "tool_name": d["tool_name"] if d else None,
                    "agent_role": d["agent_role"] if d else None,
                    "operation_class": d["operation_class"] if d else None,
                    "decided_at": d["decided_at"] if d else None,
                }
            )
        return rows

    def _v_risk_evidence(self, sql, params) -> list[dict]:
        rows = self._evidence_rows()
        if "COUNT(*) FILTER" in sql:
            total = len(rows)
            probs = [
                r["risk_probability"]
                for r in rows
                if r["risk_probability"] is not None
            ]
            lat = [
                r["inference_latency_ms"]
                for r in rows
                if r["inference_latency_ms"] is not None
            ]
            return [
                {
                    "total": total,
                    "low": sum(1 for r in rows if r["predicted_risk_class"] == "LOW"),
                    "medium": sum(
                        1 for r in rows if r["predicted_risk_class"] == "MEDIUM"
                    ),
                    "high": sum(1 for r in rows if r["predicted_risk_class"] == "HIGH"),
                    "without_decision": sum(
                        1 for r in rows if r["decision_id"] is None
                    ),
                    "unlinked_decision": sum(
                        1
                        for r in rows
                        if r["decision_id"] is not None and r["final_action"] is None
                    ),
                    "hitl_from_prediction": sum(
                        1
                        for r in rows
                        if r["final_action"] == "HITL"
                        and r["final_reason_code"] == "PREDICTED_MEDIUM_RISK"
                    ),
                    "block_from_prediction": sum(
                        1
                        for r in rows
                        if r["final_action"] == "BLOCK"
                        and r["final_reason_code"] == "PREDICTED_HIGH_RISK"
                    ),
                    "avg_probability": (sum(probs) / len(probs)) if probs else None,
                    "avg_latency_ms": (sum(lat) / len(lat)) if lat else None,
                }
            ]
        if "ORDER BY predicted_at DESC" in sql:
            if params.get("predicted_class"):
                rows = [
                    r
                    for r in rows
                    if r["predicted_risk_class"] == params["predicted_class"]
                ]
            if params.get("final_action"):
                rows = [r for r in rows if r["final_action"] == params["final_action"]]
            return rows
        return rows


def _make_store():
    return {"decisions": {}, "events": {}, "predictions": {}}


# -- Construccion del middleware real con persistencia en memoria --

def _build_middleware(
    settings: Settings,
    store: dict,
    monkeypatch,
    profile_name: str = "analyst",
    predictor: RiskPredictor | None = None,
) -> GovernanceMiddleware:
    """Arma un middleware real con AuditWriter/PredictionStore reales.

    La conexion a PostgreSQL se reemplaza por la conexion en memoria para que
    la persistencia (auditoria + predicciones) quede en `store`.
    """
    from argentgob.db import connection as connection_module

    # AuditWriter y PredictionStore importan get_connection desde
    # argentgob.db.connection dentro de sus metodos; parcheamos ese modulo.
    monkeypatch.setattr(connection_module, "get_connection", lambda: _MemConn(store))

    profile = PROFILES[profile_name]
    abac = ABACEvaluator(settings, profile)
    # Evitar el intento de conexion a PostgreSQL: usar el fallback in-memory.
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
        risk_predictor=predictor or RiskPredictor(),
        prediction_store=PredictionStore(settings),
    )


def _run_agentic_session(mw: GovernanceMiddleware, identity: AgentIdentity) -> dict:
    """Un agente analyst recorre un analisis de mercado con varias tool calls.

    Retorna un dict con el resultado de cada llamada: el texto de exito si se
    ejecuto, o el motivo del bloqueo (`blocked:<reason>`) si fue abortado.
    """
    outcomes: dict[str, str] = {}

    # 1. Consulta benigna de precio -> PASS (prediccion LOW).
    stock = _FlowSpyTool("stock_price", governance=mw, agent_identity=identity)
    stock.operation_class = "READ"
    stock.resource = "market"
    outcomes["stock_benign"] = stock._run(query="AAPL precio")

    # 2. Busqueda benigna de noticias -> PASS (prediccion LOW).
    news_ok = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    news_ok.operation_class = "EXTERNAL_SEND"
    news_ok.resource = "duckduckgo.com"
    outcomes["news_benign"] = news_ok._run(query="bitcoin mercado")

    # 3. Inyeccion SQL -> BLOCK (prediccion HIGH).
    news_inj = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    news_inj.operation_class = "EXTERNAL_SEND"
    news_inj.resource = "duckduckgo.com"
    try:
        news_inj._run(query="DROP TABLE policies;--")
        outcomes["news_injection"] = "EXECUTED"
    except HookAborted as exc:
        outcomes["news_injection"] = exc.reason

    # 4. PII en la consulta -> BLOCK (prediccion HIGH).
    news_pii = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    news_pii.operation_class = "EXTERNAL_SEND"
    news_pii.resource = "duckduckgo.com"
    try:
        news_pii._run(query="email user@test.com password=123")
        outcomes["news_pii"] = "EXECUTED"
    except HookAborted as exc:
        outcomes["news_pii"] = exc.reason

    return outcomes


# -- Tests --

@requires_model
def test_agente_analisis_mercado_valida_predice_bloquea(
    settings, monkeypatch
):
    """Un agente analyst recorre un analisis: PASS benigno, BLOCK por riesgo."""
    store = _make_store()
    mw = _build_middleware(settings, store, monkeypatch)
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")

    outcomes = _run_agentic_session(mw, identity)

    # Benignas se ejecutan (PASS).
    assert outcomes["stock_benign"].startswith("spy_executed")
    assert outcomes["news_benign"].startswith("spy_executed")
    # Inyeccion y PII se bloquean por prediccion HIGH.
    assert "PREDICTED_HIGH_RISK" in outcomes["news_injection"]
    assert "PREDICTED_HIGH_RISK" in outcomes["news_pii"]
    # La auditoria persistio 4 decisiones y 2 eventos (solo las ejecutadas).
    assert len(store["decisions"]) == 4
    assert len(store["events"]) == 2
    assert all(ev["status"] == "SUCCESS" for ev in store["events"].values())


@requires_model
def test_decisiones_reflejadas_en_consola(settings, monkeypatch):
    """El dashboard (ConsoleQueryService) refleja PASS/BLOCK del flujo."""
    store = _make_store()
    mw = _build_middleware(settings, store, monkeypatch)
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")
    _run_agentic_session(mw, identity)

    service = ConsoleQueryService(connection_factory=lambda: _MemConn(store))
    metrics = service.metrics()

    assert metrics["total"] == 4
    assert metrics["pass"] == 2
    assert metrics["block"] == 2
    assert metrics["hitl"] == 0


@requires_model
def test_predicciones_reflejadas_en_evidencia(settings, monkeypatch):
    """El dashboard (PredictionQueryService) refleja las predicciones."""
    store = _make_store()
    mw = _build_middleware(settings, store, monkeypatch)
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")
    _run_agentic_session(mw, identity)

    service = PredictionQueryService(connection_factory=lambda: _MemConn(store))
    metrics = service.metrics()

    assert metrics["total"] == 4
    assert metrics["low"] == 2
    assert metrics["high"] == 2
    # Dos bloqueos originados por prediccion HIGH.
    assert metrics["block_from_prediction"] == 2
    assert metrics["hitl_from_prediction"] == 0


@requires_model
def test_auditoria_registra_decisiones_sin_payload_crudo(settings, monkeypatch):
    """INV-05: la auditoria persiste digests, nunca el payload crudo."""
    store = _make_store()
    mw = _build_middleware(settings, store, monkeypatch)
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")
    _run_agentic_session(mw, identity)

    for decision in store["decisions"].values():
        assert decision["payload_digest"].startswith("sha256:")
        assert "query" not in decision  # sin argumentos crudos
    # Las predicciones tampoco guardan el payload.
    for pred in store["predictions"].values():
        assert "query" not in pred


@requires_model
def test_herramienta_no_permitida_bloquea_por_politica(settings, monkeypatch):
    """Un perfil restringido bloquea news por politica (TOOL_NOT_ALLOWED)."""
    store = _make_store()
    mw = _build_middleware(
        settings, store, monkeypatch, profile_name="analyst_restricted"
    )
    identity = AgentIdentity(id="agente-restricted-001", role="analyst_restricted")

    tool = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"
    with pytest.raises(HookAborted) as exc:
        tool._run(query="bitcoin")
    assert tool.call_count == 0
    assert "TOOL_NOT_ALLOWED" in exc.value.reason
    # La decision quedo persistida en la auditoria.
    assert len(store["decisions"]) == 1
    decision = next(iter(store["decisions"].values()))
    assert decision["action"] == "BLOCK"
    assert decision["reason_code"] == "TOOL_NOT_ALLOWED"


@requires_model
def test_prediccion_medium_eleva_a_hitl_reflejado(settings, monkeypatch):
    """Una prediccion MEDIUM eleva un PASS a HITL y se refleja en el dashboard."""
    store = _make_store()
    predictor = RiskPredictor()
    real_predict = predictor.predict

    def forced_medium(features):
        pred = real_predict(features)
        if pred is None:
            return None
        from argentgob.analytics.predictor import RiskPrediction

        return RiskPrediction(
            predicted_risk_class="MEDIUM",
            risk_probability=pred.class_probabilities["MEDIUM"],
            class_probabilities=pred.class_probabilities,
            model_id=pred.model_id,
            model_version=pred.model_version,
            feature_schema_version=pred.feature_schema_version,
            inference_latency_ms=pred.inference_latency_ms,
        )

    predictor.predict = forced_medium
    mw = _build_middleware(settings, store, monkeypatch, predictor=predictor)
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")

    tool = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"
    with pytest.raises(HookAborted) as exc:
        tool._run(query="bitcoin mercado")
    assert tool.call_count == 0
    assert "HUMAN_APPROVAL_REQUIRED" in exc.value.reason

    # El dashboard refleja el HITL originado por prediccion.
    console = ConsoleQueryService(connection_factory=lambda: _MemConn(store))
    assert console.metrics()["hitl"] == 1
    evidence = PredictionQueryService(connection_factory=lambda: _MemConn(store))
    assert evidence.metrics()["hitl_from_prediction"] == 1
