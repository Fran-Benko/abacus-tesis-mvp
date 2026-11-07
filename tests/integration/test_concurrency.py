"""Test de concurrencia entre agentes (hito de prioridad alta).

Cierra el sesgo 3 de `docs/analytics/0004-analisis-tests-integracion.md`
("Concurrencia"): no se validaban carreras entre agentes. Este test verifica:

1. **Aislamiento por agente**: dos agentes ejecutando analisis agencios en
   paralelo no mezclan sus decisiones (cada `decision_id`/`event_id` pertenece
   a su agente).
2. **Unicidad**: no hay colisiones de `event_id` entre los dos flujos.
3. **Idempotencia H6**: dos llamadas de approval con la misma
   `idempotency_key` (mismo evento) producen una sola fila (no duplicados).
4. **Reflejo agregado**: el dashboard (ConsoleQueryService) muestra las
   decisiones de ambos agentes.

Usa la conexion en memoria compartida (`_mem_db.py`), que es thread-safe
(RLock por conexion).
"""
from __future__ import annotations

import threading
from datetime import UTC

import pytest

from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import DEFAULT_MODEL_DIR, RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.console.service import ConsoleQueryService
from argentgob.core.envelope import AgentIdentity
from argentgob.core.errors import HookAborted
from argentgob.db.audit import AuditWriter
from argentgob.hitl.approval_service import ApprovalService
from argentgob.hitl.argument_hold import InMemoryArgumentHold
from argentgob.hitl.notifier import ApprovalNotifier, ConsoleNotifier
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
    def __init__(self):
        self.store = _new_store()

    def factory(self):
        return MemConn(self.store)


def _build_middleware(settings, monkeypatch, db, profile_name="analyst"):
    """Middleware real con persistencia en memoria compartida."""
    profile = PROFILES[profile_name]
    abac = ABACEvaluator(settings, profile)
    monkeypatch.setattr(abac, "_load_allowed_tools", lambda: list(profile.allowed_tools))
    guardrails = GuardrailEngine(
        profile_guardrails=profile.active_guardrails,
        rate_limit=settings.rate_limit_calls_per_run,
    )
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=guardrails,
        audit=AuditWriter(settings),
        hold=InMemoryArgumentHold(settings),
        approval_service=ApprovalService(settings, connection_factory=db.factory),
        notifier=ApprovalNotifier(channel=ConsoleNotifier()),
        feature_builder=FeatureBuilder(),
        risk_predictor=RiskPredictor(),
        prediction_store=PredictionStore(settings),
    )


def _agent_session(mw, agent_id, barrier, outcomes):
    """Un agente recorre: PASS benigno + BLOCK por inyeccion (HIGH)."""
    identity = AgentIdentity(id=agent_id, role="analyst")
    barrier.wait()  # sincroniza el inicio de ambos agentes

    # 1. Benigna -> PASS.
    tool = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    tool.operation_class = "EXTERNAL_SEND"
    tool.resource = "duckduckgo.com"
    try:
        outcomes[f"{agent_id}:benign"] = tool._run(query="bitcoin mercado")
    except HookAborted as exc:
        outcomes[f"{agent_id}:benign"] = f"blocked:{exc.reason}"

    # 2. Inyeccion -> BLOCK (prediccion HIGH o guardrail).
    tool2 = _FlowSpyTool("news", governance=mw, agent_identity=identity)
    tool2.operation_class = "EXTERNAL_SEND"
    tool2.resource = "duckduckgo.com"
    try:
        tool2._run(query="DROP TABLE policies;--")
        outcomes[f"{agent_id}:injection"] = "EXECUTED"
    except HookAborted as exc:
        outcomes[f"{agent_id}:injection"] = exc.reason


@requires_model
def test_dos_agentes_concurrentes_aislan_decisiones(settings, monkeypatch):
    """Dos agentes en paralelo: decisiones separadas, sin colisiones."""
    from argentgob.db import connection as connection_module

    db = _MemDb()
    monkeypatch.setattr(connection_module, "get_connection", db.factory)
    mw = _build_middleware(settings, monkeypatch, db)

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}
    t1 = threading.Thread(
        target=_agent_session, args=(mw, "agente-A", barrier, outcomes)
    )
    t2 = threading.Thread(
        target=_agent_session, args=(mw, "agente-B", barrier, outcomes)
    )
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive(), "los hilos no terminaron"

    # Ambos pasaron su llamada benigna y bloquearon la inyeccion.
    for a in ("agente-A", "agente-B"):
        assert outcomes[f"{a}:benign"].startswith("spy_executed")
        assert "blocked:" in outcomes[f"{a}:injection"]

    # Aislamiento: las 4 decisiones (2 por agente) tienen event/agent correcto.
    decisions = list(db.store["governance_decisions"].values())
    assert len(decisions) == 4
    by_agent: dict[str, int] = {}
    event_ids: set[str] = set()
    for d in decisions:
        by_agent[d["agent_id"]] = by_agent.get(d["agent_id"], 0) + 1
        assert d["event_id"] not in event_ids, "colision de event_id"
        event_ids.add(d["event_id"])
    assert by_agent == {"agente-A": 2, "agente-B": 2}

    # Ambas sesiones escribieron un resultado exitoso (PASS).
    events = list(db.store["governance_events"].values())
    assert len(events) == 2

    # Dashboard: refleja las decisiones de ambos agentes.
    console = ConsoleQueryService(connection_factory=db.factory)
    m = console.metrics()
    assert m["total"] == 4
    assert m["pass"] == 2
    assert m["block"] == 2


@requires_model
def test_idempotencia_approval_sin_duplicados(settings, monkeypatch):
    """Misma idempotency_key -> una sola approval (no duplicados)."""
    from datetime import datetime

    from argentgob.db import connection as connection_module

    db = _MemDb()
    monkeypatch.setattr(connection_module, "get_connection", db.factory)
    mw = _build_middleware(settings, monkeypatch, db)

    identity = AgentIdentity(id="agente-dup", role="analyst")
    # Dos ejecuciones del MISMO evento (misma idempotency_key) en paralelo.
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def attempt(barrier, results, tag):
        tool = _FlowSpyTool("news", governance=mw, agent_identity=identity)
        tool.operation_class = "EXTERNAL_SEND"
        tool.resource = "duckduckgo.com"
        barrier.wait()
        try:
            tool._run(query="identico payload")
            results[tag] = "EXECUTED"
        except HookAborted as exc:
            results[tag] = exc.reason

    t1 = threading.Thread(target=attempt, args=(barrier, results, "a"))
    t2 = threading.Thread(target=attempt, args=(barrier, results, "b"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive()

    # La approval es idempotente por idempotency_key -> 1 sola fila.
    approvals = list(db.store["approval_requests"].values())
    idem_keys = {a["idempotency_key"] for a in approvals}
    assert len(idem_keys) == len(approvals), "claves idempotentes duplicadas"

    approval_service = ApprovalService(settings, connection_factory=db.factory)

    def _capture_hold():
        return lambda: True

    first = approval_service.create_pending_and_issue_token(
        event_id="ev-idem-1",
        decision_id="dec-idem-1",
        agent_id="agente-dup",
        tool_name="news",
        argument_digest="sha256:same",
        idempotency_key="hitl:ev-idem-1",
        argued_at=datetime.now(UTC),
        expires_at=None,
        hold_registered=_capture_hold(),
    )
    second = approval_service.create_pending_and_issue_token(
        event_id="ev-idem-1",
        decision_id="dec-idem-1",
        agent_id="agente-dup",
        tool_name="news",
        argument_digest="sha256:same",
        idempotency_key="hitl:ev-idem-1",
        argued_at=datetime.now(UTC),
        expires_at=None,
        hold_registered=_capture_hold(),
    )
    assert first[0].approval_id == second[0].approval_id, (
        "misma idempotency_key deberia devolver la misma approval"
    )


@requires_model
def test_concurrencia_no_pierde_payload_digests(settings, monkeypatch):
    """Cada decision conserva su digest sin contaminacion entre agentes."""
    from argentgob.db import connection as connection_module

    db = _MemDb()
    monkeypatch.setattr(connection_module, "get_connection", db.factory)
    mw = _build_middleware(settings, monkeypatch, db)

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}
    t1 = threading.Thread(
        target=_agent_session, args=(mw, "agente-X", barrier, outcomes)
    )
    t2 = threading.Thread(
        target=_agent_session, args=(mw, "agente-Y", barrier, outcomes)
    )
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # Cada decision tiene un payload_digest sha256 valido.
    decisions = list(db.store["governance_decisions"].values())
    assert len(decisions) == 4
    for d in decisions:
        assert d["payload_digest"].startswith("sha256:")
    # Los digests de las decisiones benignas son iguales (mismo payload), los
    # de las inyecciones iguales entre si: sin contaminacion.
    benign_digests = {
        d["payload_digest"] for d in decisions if d["reason_code"] == "ALLOWED"
    }
    block_digests = {
        d["payload_digest"] for d in decisions if d["reason_code"] != "ALLOWED"
    }
    assert len(benign_digests) == 1  # ambas benignas: mismo payload
    assert len(block_digests) == 1  # ambas inyecciones: mismo payload