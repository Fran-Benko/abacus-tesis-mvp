"""HITL end-to-end con el modelo activo (hito de prioridad alta).

Cierra el sesgo 3.5 de `docs/analytics/0004-analisis-tests-integracion.md`:
hasta ahora los tests validaban la resolucion de una approval pre-existente
(datos fijos), pero NO que una prediccion MEDIUM del modelo activo genere la
approval, que el HITL bloquee la ejecucion y que la resolucion aprobada se
refleje en el flujo.

Este test reproduce el flujo completo con componentes reales (solo la conexion
a PostgreSQL se sustituye por la conexion en memoria de `_mem_db.py`):

    prediccion MEDIUM (modelo activo)
        -> approval PENDING + token de resume (ApprovalService real)
        -> hold de argumentos (InMemoryArgumentHold real)
        -> HookAborted HUMAN_APPROVAL_REQUIRED (la tool NO ejecuta)
        -> resolucion APPROVED por auditor (ResolutionService + evidencia)
        -> GovernedResumeService.resume(token)  (consume token, capability 1-uso)
        -> ExecutionOrchestrator.execute_governed() (ejecuta la tool 1 vez)
        -> reflejo en dashboard: decision HITL/PREDICTED_MEDIUM_RISK en
           v_governance_console y prediccion MEDIUM en v_risk_evidence.

INV-05: ni la approval, ni el resume, ni la ejecucion persisten el payload
crudo (solo digests).
"""
from __future__ import annotations

import pytest

from argentgob.analytics.evidence import PredictionQueryService
from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import DEFAULT_MODEL_DIR, RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.console.hitl_detail import HitlDetailService
from argentgob.console.resolution import ResolutionService
from argentgob.console.service import ConsoleQueryService
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import HookAborted
from argentgob.db.audit import AuditWriter
from argentgob.hitl.approval_service import ApprovalService
from argentgob.hitl.argument_hold import InMemoryArgumentHold
from argentgob.hitl.execution import ExecutionOrchestrator
from argentgob.hitl.governed_resume import GovernedResumeService
from argentgob.hitl.notifier import ApprovalNotifier
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.module_c.profiles import PROFILES
from tests.integration._mem_db import MemConn, _new_store

MODEL_EXISTS = (DEFAULT_MODEL_DIR / "pipeline.joblib").exists()

requires_model = pytest.mark.skipif(
    not MODEL_EXISTS,
    reason="modelo versionado no presente",
)


class _FlowSpyTool(SpyTool):
    """SpyTool con nombre configurable para usar herramientas del catalogo."""

    def __init__(self, tool_name: str, **kwargs):
        super().__init__(**kwargs)
        self.name = tool_name


class _MemDb:
    """Fabrica de conexiones en memoria: comparte el mismo store (como una DB)."""

    def __init__(self):
        self.store = _new_store()

    def factory(self):
        return MemConn(self.store)


class _NotificationCapture:
    """Canal que captura las notificaciones HITL (no sensible)."""

    def __init__(self):
        self.notifications = []

    def notify(self, *, notification, destination=None) -> bool:
        self.notifications.append(notification)
        return True


@requires_model
def test_hitl_end_to_end_con_modelo_activo(settings, monkeypatch):
    """Prediccion MEDIUM -> approval -> resolucion aprobada -> resume -> ejecucion."""
    from argentgob.db import connection as connection_module

    db = _MemDb()
    # Todos los servicios del flujo usan la misma conexion en memoria.
    monkeypatch.setattr(connection_module, "get_connection", db.factory)

    # -- Componentes reales (igual que en produccion: crew.py) ----------
    profile = PROFILES["analyst"]
    abac = ABACEvaluator(settings, profile)
    monkeypatch.setattr(
        abac, "_load_allowed_tools", lambda: list(profile.allowed_tools)
    )
    guardrails = GuardrailEngine(
        profile_guardrails=profile.active_guardrails,
        rate_limit=settings.rate_limit_calls_per_run,
    )
    audit = AuditWriter(settings)
    hold = InMemoryArgumentHold(settings)
    approval_service = ApprovalService(settings, connection_factory=db.factory)
    capture = _NotificationCapture()
    notifier = ApprovalNotifier(channel=capture)

    # El plan claro del token se genera dentro del ApprovalService y solo se
    # entrega a su caller (el middleware). Para poder reanudar el resume con
    # el token real lo capturamos con un wrapper que conserva el retorno.
    captured_token_plan: dict[str, str] = {}
    _original_create = approval_service.create_pending_and_issue_token

    def _capturing_create_pending_and_issue_token(**kw):
        nonlocal captured_token_plan
        result = _original_create(**kw)
        captured_token_plan["token_id"] = result[1]
        captured_token_plan["token_plan"] = result[2]
        return result

    approval_service.create_pending_and_issue_token = (
        _capturing_create_pending_and_issue_token
    )

    # Modelo activo con prediccion forzada a MEDIUM (mismo patron que el test
    # de flujos agencios): usa el RiskPredictor real, solo cambia la clase.
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

    middleware = GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=audit,
        hold=hold,
        approval_service=approval_service,
        notifier=notifier,
        feature_builder=FeatureBuilder(),
        risk_predictor=predictor,
        prediction_store=PredictionStore(settings),
    )

    # -- 1. El agente ejecuta una tool call benigna con prediccion MEDIUM --
    identity = AgentIdentity(id="agente-analyst-001", role="analyst")
    tool = _FlowSpyTool("news", governance=middleware, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"

    with pytest.raises(HookAborted) as exc:
        tool._run(query="bitcoin mercado")
    assert tool.call_count == 0  # INV-02: HITL no ejecuta
    assert "HUMAN_APPROVAL_REQUIRED" in exc.value.reason

    # -- 2. La prediccion MEDIUM quedo registrada ----------------------
    predicates = db.store["risk_predictions"]
    assert predicates, "la prediccion debe persistirse"
    pred = next(iter(predicates.values()))
    assert pred["predicted_risk_class"] == "MEDIUM"

    # -- 3. Se creo una approval PENDING con token de resume -----------
    approvals = list(db.store["approval_requests"].values())
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["state"] == "PENDING"
    assert approval["tool_name"] == "news"
    # El token se emitio (hold registrado) y solo se persiste su hash.
    tokens = list(db.store["approval_resume_tokens"].values())
    assert len(tokens) == 1
    assert tokens[0]["state"] == "ACTIVE"
    assert tokens[0]["token_hash"].startswith("sha256:")
    # El plan claro se capturo en el wrapper (solo su hash se persiste).
    assert captured_token_plan["token_id"]
    assert captured_token_plan["token_plan"]

    # -- 4. Resolucion APPROVED por un auditor con evidencia vigente ----
    provider = AuditorIdentityProvider(
        environment="TEST",
        development_only=True,
        fixture_roles={"auditor-1": ["viewer", "auditor"]},
    )
    evidence = provider.authenticate(identifier="auditor-1", role=AppRole.AUDITOR)
    resolver = ResolutionService(
        identity_provider=provider,
        resolver=__import__(
            "argentgob.hitl.resolver", fromlist=["ApprovalResolver"]
            ).ApprovalResolver(
                settings,
                auditor_roles=["auditor"],
                connection_factory=db.factory,
            ),
            detail_service=HitlDetailService(connection_factory=db.factory),
        )
    outcome = resolver.resolve(
        approval_id=approval["approval_id"],
        decision="APPROVED",
        evidence=evidence,
    )
    assert outcome["outcome"] == "approved"
    approved = db.store["approval_requests"][approval["approval_id"]]
    assert approved["state"] == "APPROVED"
    assert approved["resolved_by"] == "auditor-1"

    # -- 5. Resume con el token: capability de un solo uso --------------
    resume = GovernedResumeService(
        settings=settings,
        hold=hold,
        connection_factory=db.factory,
    )
    capability = resume.resume(
        token_id=captured_token_plan["token_id"],
        presented_token=captured_token_plan["token_plan"],
    )
    assert capability is not None
    assert capability.tool_name == "news"
    assert capability.event_id == approval["event_id"]
    assert capability.consumed is False
    # El token quedo REDEEMED (un solo uso).
    assert (
        db.store["approval_resume_tokens"][captured_token_plan["token_id"]]["state"]
        == "REDEEMED"
    )

    # -- 6. Ejecucion gobernada: ejecuta la tool una sola vez ----------
    executed = {"calls": 0}

    def executor(args):
        executed["calls"] += 1
        return "executed"

    from argentgob.core.envelope import ToolCallEnvelope

    envelope = ToolCallEnvelope.build(
        agent=identity,
        tool_name=capability.tool_name,
        operation_class="EXTERNAL_SEND",
        resource="duckduckgo.com",
        environment=settings.environment,
        execution_arguments={"query": "bitcoin mercado"},
    )
    orch = ExecutionOrchestrator(settings, connection_factory=db.factory)
    result = orch.execute_governed(
        capability=capability, envelope=envelope, executor=executor
    )
    assert result == "governed:SUCCESS"
    assert executed["calls"] == 1  # exactamente una vez
    # La capability quedo consumida (no re-ejecutable).
    assert capability.consumed is True
    # El resultado se persistio en execution_results.
    results = list(db.store["execution_results"].values())
    assert len(results) == 1
    assert results[0]["status"] == "SUCCESS"
    assert results[0]["event_id"] == envelope.event_id

    # -- 7. Reflejo en dashboard ---------------------------------------
    # Consola: la decision HITL (PREDICTED_MEDIUM_RISK) queda en
    # governance_decisions; la verificamos via el servicio.
    console = ConsoleQueryService(connection_factory=db.factory)
    metrics = console.metrics()
    assert metrics["hitl"] == 1
    decisions = console.list_decisions()
    assert decisions
    assert decisions[0]["action"] == "HITL"
    assert decisions[0]["reason_codes"] == ["PREDICTED_MEDIUM_RISK"]

    # Evidencia predictiva: la prediccion MEDIUM y el HITL originado.
    evidence_svc = PredictionQueryService(connection_factory=db.factory)
    ev_metrics = evidence_svc.metrics()
    assert ev_metrics["medium"] == 1
    assert ev_metrics["hitl_from_prediction"] == 1
    preds = evidence_svc.list_predictions()
    assert preds
    assert preds[0]["predicted_risk_class"] == "MEDIUM"
    assert preds[0]["final_action"] == "HITL"
    assert preds[0]["final_reason_code"] == "PREDICTED_MEDIUM_RISK"
    # MEDIUM sugiere HITL y el final es HITL: consistente (sin divergencia).
    assert preds[0]["divergence"] == "CONSISTENTE"

    # -- 8. INV-05: nada de payload crudo persistido --------------------
    for table in ("approval_requests", "approval_resume_tokens", "execution_results"):
        for row in db.store[table].values():
            serialized = str(row)
            assert "bitcoin mercado" not in serialized, (
                f"payload crudo en {table}: {row}"
            )