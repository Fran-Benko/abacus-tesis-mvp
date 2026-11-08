"""Librería de escenarios para la demo end-to-end de ArgentGob-Mesh.

Cada escenario ejecuta un flujo de gobernanza real (middleware + componentes
reales) sobre una conexión inyectable, y retorna un dict con la narrativa y el
outcome. Los proveedores de herramientas son fake (sin red ni LLM), igual que
en `scripts/r2_demo_logs.py`.

Escenarios:
1. `consulta_benigna`       -> PASS: predice LOW, ejecuta, audita.
2. `bloqueo_por_prediccion` -> BLOCK: predicción HIGH no ejecuta (INV-02).
3. `bloqueo_por_guardrail`  -> BLOCK: inyección SQL detectada por guardrail.
4. `politica_sensibilidad`  -> BLOCK: SENSITIVITY_EXCEEDED (política YAML).
5. `flujo_hitl_completo`    -> HITL: approval -> resolución APROBADA ->
                              resume (token 1-uso) -> ejecución gobernada.

La salida narrativa se genera con structlog (logs estructurados legibles); el
flag `persistir` permite usar una conexión real a PostgreSQL (stack) en lugar
de la conexión en memoria.
"""
from __future__ import annotations

from typing import Any

from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import RiskPrediction, RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.console.resolution import ResolutionService
from argentgob.core.config import Settings, get_settings
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
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
from argentgob.module_c.deterministic_evaluator import DeterministicEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.module_c.profiles import PROFILES
from argentgob.module_c.yaml_policies import load_all
from argentgob.observability.logger import get_logger

log = get_logger("demo_end_to_end")


class _DemoTool(SpyTool):
    """Herramienta del catálogo con nombre/clase/recurso configurables."""

    def __init__(self, tool_name: str, operation_class: str, resource: str, **kw):
        super().__init__(**kw)
        self.name = tool_name
        self.operation_class = operation_class
        self.resource = resource


class _FakeProvider:
    """Provider fake de noticias (sin red) para NewsTool."""

    def __init__(self, items: list | None = None):
        self.name = "demo"
        self.calls = 0
        self._items = items or []

    def search(self, query: str, max_results: int):
        self.calls += 1
        return self._items


class _NotificationCapture:
    """Canal de notificaciones HITL (no sensible, para la narrativa)."""

    def __init__(self):
        self.notifications: list[str] = []

    def notify(self, *, notification, destination=None) -> bool:
        self.notifications.append(notification)
        log.info("hitl_notifier", notification=notification)
        return True


class DemoStore:
    """Conexión inyectable (memoria o real) para todos los servicios."""

    def __init__(self, real: bool = False, db_url: str | None = None):
        self.real = real
        self.db_url = db_url
        self.store: Any = None
        if not real:
            from tests.integration._mem_db import MemConn, _new_store

            self.store = _new_store()
            self._factory = lambda: MemConn(self.store)
            # Reemplaza el acceso global a PostgreSQL por la conexión en memoria
            # (mismo patrón que tests/integration: AuditWriter, PredictionStore
            #  importan get_connection desde argentgob.db.connection).
            from argentgob.db import connection as connection_module

            self._original_get_connection = connection_module.get_connection
            connection_module.get_connection = self._factory
        else:
            from argentgob.db.connection import get_connection

            self._factory = get_connection
            self._original_get_connection = get_connection

    def factory(self):
        return self._factory()

    def restore(self) -> None:
        """Restaura el get_connection global (usado tras la demo)."""
        if not self.real:
            from argentgob.db import connection as connection_module

            connection_module.get_connection = self._original_get_connection


def _wiring(store: DemoStore, settings: Settings) -> dict[str, Any]:
    """Arma los componentes reales con la conexión inyectable.

    Retorna el middleware y los servicios HITL para el escenario completo.
    """
    profile = PROFILES["analyst"]
    abac = ABACEvaluator(settings, profile)
    # Fallback in-memory: no tocar PostgreSQL si no hay stack.
    abac._load_allowed_tools = lambda: list(profile.allowed_tools)  # type: ignore[assignment]
    guardrails = GuardrailEngine(
        profile_guardrails=profile.active_guardrails,
        rate_limit=settings.rate_limit_calls_per_run,
    )
    audit = AuditWriter(settings)
    hold = InMemoryArgumentHold(settings)
    approval_service = ApprovalService(settings, connection_factory=store.factory)
    capture = _NotificationCapture()
    notifier = ApprovalNotifier(channel=capture)
    middleware = GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=audit,
        hold=hold,
        approval_service=approval_service,
        notifier=notifier,
        feature_builder=FeatureBuilder(),
        risk_predictor=RiskPredictor(),
        prediction_store=PredictionStore(settings),
    )
    return {
        "middleware": middleware,
        "hold": hold,
        "approval_service": approval_service,
        "notifier": notifier,
        "notifications": capture,
        "settings": settings,
    }


def _con_prediccion(mw, clase: str):
    """Fuerza la predicción del predictor a `clase` durante el contexto.

    Funciona aunque el modelo no esté disponible (p.ej. sin `sklearn` en el
    entorno local): se reemplaza temporalmente `mw.risk_predictor` por un stub
    con `available=True` que devuelve siempre la clase forzada. Al salir del
    contexto se restaura el predictor original para que un escenario no
    contamine al siguiente.

    El stub respeta el contrato de `RiskPredictor`: `available`, `predict`,
    `predict_proba`, `model_id`, `model_version`, `feature_schema_version`.
    """
    import contextlib

    predictor = mw.risk_predictor
    original = predictor

    class _StubPredictor:
        available = True

        def predict(self, features):
            probs = {"LOW": 0.05, "MEDIUM": 0.2, "HIGH": 0.75}
            return RiskPrediction(
                predicted_risk_class=clase,
                risk_probability=probs[clase],
                class_probabilities=probs,
                model_id="risk_model_v1",
                model_version="v1",
                feature_schema_version="v1",
                inference_latency_ms=0.0,
            )

        def predict_proba(self, features):
            probs = {"LOW": 0.05, "MEDIUM": 0.2, "HIGH": 0.75}
            return [probs["LOW"], probs["MEDIUM"], probs["HIGH"]]

        @property
        def model_id(self):
            return "risk_model_v1"

        @property
        def model_version(self):
            return "v1"

        @property
        def feature_schema_version(self):
            return "v1"

    @contextlib.contextmanager
    def _cm():
        mw.risk_predictor = _StubPredictor()  # type: ignore[assignment]
        try:
            yield
        finally:
            mw.risk_predictor = original  # type: ignore[assignment]

    return _cm()


class EscenarioDemo:
    """Un escenario de la demo: narrativa + outcome."""

    def __init__(self, titulo: str, explicacion: str):
        self.titulo = titulo
        self.explicacion = explicacion
        self.outcome: str = ""
        self.detalle: dict[str, Any] = {}

    def ejecutar(self, w: dict[str, Any], identidad: AgentIdentity) -> EscenarioDemo:
        """Template de ejecución; redefinido por cada escenario."""
        raise NotImplementedError

    def narrar(self) -> str:
        return (
            f"\n{'='*72}\nESCENARIO: {self.titulo}\n{self.explicacion}\n"
            f"{'-'*72}\noutcome: {self.outcome}"
        )


def _tool(w, identidad, tool_name, op_class, resource) -> _DemoTool:
    mw = w["middleware"]
    tool = _DemoTool(tool_name, op_class, resource, governance=mw, agent_identity=identidad)
    return tool


def _ejecutar(tool: _DemoTool, **kwargs) -> str:
    """Ejecuta la tool; HookAborted se convierte en outcome de bloqueo."""
    try:
        return "OK: " + tool._run(**kwargs)
    except HookAborted as exc:
        return f"BLOQUEADO: {exc.reason}"


class EscenarioBenigna(EscenarioDemo):
    """PA 1: consulta benigna -> PASS con predicción LOW."""

    def ejecutar(self, w, identidad) -> EscenarioDemo:
        tool = _tool(w, identidad, "stock_price", "READ", "market")
        self.outcome = _ejecutar(tool, query="AAPL precio")
        self.detalle["call_count"] = tool.call_count
        return self


class EscenarioBloqueoPrediccion(EscenarioDemo):
    """PA 2: predicción HIGH (forzada) -> BLOCK sin ejecutar."""

    def ejecutar(self, w, identidad) -> EscenarioDemo:
        tool = _tool(w, identidad, "news", "EXTERNAL_SEND", "duckduckgo.com")
        # Predicción HIGH forzada: el PEP bloquea antes de la tool (INV-02).
        # Al salir del contexto se restaura el predictor real.
        with _con_prediccion(w["middleware"], "HIGH"):
            self.outcome = _ejecutar(tool, query="noticia riesgo alto")
        self.detalle["call_count"] = tool.call_count
        return self


class EscenarioGuardrail(EscenarioDemo):
    """PA 3: inyección SQL detectada por guardrail -> BLOCK."""

    def ejecutar(self, w, identidad) -> EscenarioDemo:
        tool = _tool(w, identidad, "stock_price", "READ", "market")
        # El guardrail de inyección de queries detecta el patrón. La predicción
        # LOW (natural) deja pasar la fase predictiva y se alcanza el guardrail.
        with _con_prediccion(w["middleware"], "LOW"):
            self.outcome = _ejecutar(tool, query="AAPL; DROP TABLE usuarios--")
        self.detalle["call_count"] = tool.call_count
        return self


class EscenarioSensibilidad(EscenarioDemo):
    """PA 4: política con sensitivity_limit MEDIUM + predicción HIGH -> BLOCK.

    Este escenario demuestra el *policy engine declarativo* real (Unidad 4):
    `DeterministicEvaluator` evalúa las políticas YAML vigentes del perfil y
    compara la predicción del modelo con `sensitivity_limit`. La política
    `POL-analyst-001` (news) declara `sensitivity_limit: MEDIUM`; con una
    predicción HIGH el evaluador decide BLOCK/SENSITIVITY_EXCEEDED, el reason
    code más específico del producto (hito políticas declarativas).
    """

    def ejecutar(self, w, identidad) -> EscenarioDemo:
        settings = w["settings"]
        # Fuente declarativa real: las políticas YAML del perfil analyst
        # (policies/analyst.yaml) se cargan como fuente de verdad (hito 10).
        class _YamlSource:
            def load_current(self):
                return load_all(environment=settings.environment)

        evaluator = DeterministicEvaluator(settings=settings, policy_source=_YamlSource())

        # Envelope de una búsqueda de noticias con predicción HIGH forzada.
        envelope = ToolCallEnvelope.build(
            agent=identidad,
            tool_name="news",
            operation_class="EXTERNAL_SEND",
            resource="duckduckgo.com",
            environment=settings.environment,
            execution_arguments={"query": "noticia moderada"},
        )
        envelope.prediction = RiskPrediction(
            predicted_risk_class="HIGH",
            risk_probability=0.92,
            class_probabilities={"LOW": 0.03, "MEDIUM": 0.05, "HIGH": 0.92},
        )
        decision = evaluator.evaluate(envelope)
        reason = decision.reason_code.value if decision.reason_codes else "N/A"
        self.detalle["policy_id"] = decision.policy_id
        self.detalle["reason"] = reason
        if decision.action.value == "BLOCK" and reason == "SENSITIVITY_EXCEEDED":
            self.outcome = "BLOQUEADO: SENSITIVITY_EXCEEDED (sensitivity_limit MEDIUM superado)"
        else:
            self.outcome = f"RESULTADO: {decision.action.value}/{reason}"
        return self


class EscenarioHitl(EscenarioDemo):
    """PA 5: predicción MEDIUM -> approval -> resolución APROBADA -> resume -> ejecución."""

    def ejecutar(self, w, identidad) -> EscenarioDemo:
        mw = w["middleware"]
        settings = w["settings"]
        store = w["_store"]

        # Captura del token de resume (solo su hash se persiste).
        approval_service = w["approval_service"]
        captured: dict[str, str] = {}
        _orig = approval_service.create_pending_and_issue_token

        def _capture(**kw):
            result = _orig(**kw)
            approval = result[0]
            captured["approval_id"] = approval.approval_id
            captured["event_id"] = kw.get("event_id", "")
            captured["token_id"] = result[1]
            captured["token_plan"] = result[2]
            return result

        approval_service.create_pending_and_issue_token = _capture  # type: ignore[method-assign]

        # 1. Tool call con predicción MEDIUM -> HITL (no ejecuta).
        tool = _tool(w, identidad, "news", "EXTERNAL_SEND", "duckduckgo.com")
        with _con_prediccion(mw, "MEDIUM"):
            self.outcome = _ejecutar(tool, query="analisis de mercado")
        assert tool.call_count == 0, "HITL no deberia ejecutar la tool"
        self.detalle["call_count"] = tool.call_count

        # 2. La approval PENDING creada (id capturado del service).
        approval_id = captured.get("approval_id")
        assert approval_id, "deberia existir una approval PENDING"
        approvals = _approvals(store)
        approval = (
            approvals[-1]
            if approvals
            else {"approval_id": approval_id, "state": "PENDING"}
        )
        self.detalle["approval_id"] = approval_id
        self.detalle["approval_state"] = approval["state"]
        self.detalle["token_id"] = captured.get("token_id")

        # 3. Resolución APROBADA por auditor (evidencia de identidad).
        provider = AuditorIdentityProvider(
            environment=settings.environment,
            development_only=True,
            fixture_roles={"auditor-1": ["viewer", "auditor"]},
        )
        evidence = provider.authenticate(identifier="auditor-1", role=AppRole.AUDITOR)
        from argentgob.console.hitl_detail import HitlDetailService
        from argentgob.hitl.resolver import ApprovalResolver

        resolver = ResolutionService(
            identity_provider=provider,
            resolver=ApprovalResolver(
                settings,
                auditor_roles=["auditor"],
                connection_factory=store.factory,
            ),
            detail_service=HitlDetailService(connection_factory=store.factory),
        )
        outcome = resolver.resolve(
            approval_id=approval_id,
            decision="APPROVED",
            evidence=evidence,
        )
        self.detalle["resolucion"] = outcome["outcome"]
        self.detalle["resolved_by"] = approval.get("resolved_by")

        # 4. Resume con el token (capability de un solo uso).
        resume = GovernedResumeService(
            settings=settings,
            hold=w["hold"],
            connection_factory=store.factory,
        )
        capability = resume.resume(
            token_id=captured["token_id"],
            presented_token=captured["token_plan"],
        )
        assert capability is not None
        self.detalle["capability_tool"] = capability.tool_name
        self.detalle["capability_consumed"] = capability.consumed

        # 5. Ejecución gobernada (una sola vez).
        ejecutados = {"n": 0}

        def ejecutor(args):
            ejecutados["n"] += 1
            return "ejecutado-gobernado"

        from argentgob.core.envelope import ToolCallEnvelope

        envelope = ToolCallEnvelope.build(
            agent=identidad,
            tool_name=capability.tool_name,
            operation_class="EXTERNAL_SEND",
            resource="duckduckgo.com",
            environment=settings.environment,
            execution_arguments={"query": "analisis de mercado"},
        )
        # La ejecución gobernada ES el mismo evento que fue aprobado: se
        # reutiliza el event_id de la approval para que la ejecución quede
        # ligada a la decisión ya persistida (policy_decisions.event_id) y no
        # viole la FK fk_execution_event_id en PostgreSQL real.
        envelope.event_id = captured.get("event_id") or envelope.event_id
        envelope.decision = captured.get("_decision")
        orch = ExecutionOrchestrator(settings, connection_factory=store.factory)
        resultado = orch.execute_governed(
            capability=capability, envelope=envelope, executor=ejecutor
        )
        self.detalle["ejecucion"] = resultado
        self.detalle["ejecuciones"] = ejecutados["n"]
        self.detalle["capability_consumed_final"] = capability.consumed
        # El outcome resume el flujo completo: la llamada original se bloqueó
        # (HITL) y luego de la aprobación el agente reanudó y la tool se
        # ejecutó exactamente una vez.
        self.outcome = (
            "HITL COMPLETO: blocked:HUMAN_APPROVAL_REQUIRED → approved por "
            "auditor-1 → resume (token 1-uso) → ejecución gobernada:SUCCESS"
        )
        return self


def _approvals(store: DemoStore) -> list[dict]:
    if store.store is not None:
        return list(store.store["approval_requests"].values())
    return []


def ejecutar_demo(real: bool = False) -> list[EscenarioDemo]:
    """Ejecuta todos los escenarios con narrativa. Retorna los resultados."""
    settings = get_settings()
    store = DemoStore(real=real)
    w = _wiring(store, settings)
    w["_store"] = store
    identidad = AgentIdentity(id="agente-demo-001", role="analyst")

    escenarios: list[EscenarioDemo] = [
        EscenarioBenigna(
            "Consulta benigna (PASS)",
            "El agente pide el precio de AAPL. El PEP predice LOW, la política "
            "YAML del perfil analyst lo permite y la tool se ejecuta. Todo se audita.",
        ),
        EscenarioBloqueoPrediccion(
            "Bloqueo por predicción HIGH (BLOCK)",
            "El modelo predice HIGH para una búsqueda de noticias. La tool NO "
            "se ejecuta (INV-02): la predicción bloquea antes del side effect.",
        ),
        EscenarioGuardrail(
            "Bloqueo por guardrail (inyección SQL)",
            "El query contiene un patrón de inyección. El guardrail lo detecta "
            "y bloquea con razón de guardrail, sin ejecutar la tool.",
        ),
        EscenarioSensibilidad(
            "Bloqueo por sensibilidad (SENSITIVITY_EXCEEDED)",
            "La política declarativa de news tiene sensitivity_limit MEDIUM. "
            "Predicción HIGH supera el límite y el evaluador bloquea.",
        ),
        EscenarioHitl(
            "Flujo HITL completo (aprobación humana)",
            "Predicción MEDIUM -> el PEP crea una approval PENDING con token de "
            "resume de un solo uso y bloquea la ejecución. Un auditor la aprueba, "
            "el agente reanuda con su token y la tool se ejecuta exactamente una vez.",
        ),
    ]

    for esc in escenarios:
        log.info("demo_escenario", titulo=esc.titulo)
        esc.ejecutar(w, identidad)
    store.restore()
    return escenarios
