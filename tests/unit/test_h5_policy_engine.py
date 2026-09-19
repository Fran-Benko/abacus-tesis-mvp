"""Tests de H5 — Policy Engine persistente.

Cubren Unidades 3 (carga y caché), 4 (decisión determinista) y 5 (round-trip),
junto con el parsing JSON del PolicyStore. No requieren PostgreSQL: el
`_load_from_db` se mockea y la caché sirve políticas in-memory.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import AgentIdentity, ToolCallEnvelope
from argentgob.core.errors import (
    GovernanceAction,
    Obligation,
    ReasonCode,
    RiskLevel,
)
from argentgob.module_c.deterministic_evaluator import DeterministicEvaluator
from argentgob.module_c.policy import GovernancePolicy, select_effective_arguments_h5
from argentgob.module_c.policy_store import DEFAULT_POLICY_TTL_SECONDS, PolicyStore


# ── Helpers ─────────────────────────────────────────────────────────
def _policy(**overrides) -> GovernancePolicy:
    """Construye una GovernancePolicy con defaults de prueba."""
    base = dict(
        policy_id="POL-TEST",
        policy_version=1,
        profile_name="analyst",
        tool_name="stock_price",
        operation_class="READ",
        environment="TEST",
        resource=None,
        effect=GovernanceAction.PASS,
        priority=50,
        sensitivity_limit=None,
        obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        transform_spec=None,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        valid_until=None,
        policy_digest="sha256:test",
    )
    base.update(overrides)
    return GovernancePolicy(**base)


def _envelope(
    tool_name="stock_price",
    operation_class="READ",
    resource="yahoo_finance",
    role="analyst",
    environment="TEST",
    args=None,
) -> ToolCallEnvelope:
    return ToolCallEnvelope.build(
        agent=AgentIdentity(id="a-1", role=role),
        tool_name=tool_name,
        operation_class=operation_class,
        resource=resource,
        environment=environment,
        execution_arguments=args or {"query": "AAPL"},
    )


def _store_with(rows, *, ttl=None) -> PolicyStore:
    store = PolicyStore.__new__(PolicyStore)
    store.settings = None
    store.ttl_seconds = ttl or DEFAULT_POLICY_TTL_SECONDS
    store._cache = None
    store._lock = __import__("threading").Lock()
    store._db_available = True
    return store


# ── Unidad 3: carga y caché limitada ────────────────────────────────
def test_cache_sirve_politicas_con_patch_db():
    store = _store_with([])
    rows = [_policy(policy_id="P1"), _policy(policy_id="P2", tool_name="news")]
    with patch.object(store, "_load_from_db", return_value=rows):
        assert store.load_current() == rows
    assert store._cache is not None
    # Segunda llamada: usa caché sin tocar DB.
    calls = {"n": 0}

    def _db():
        calls["n"] += 1
        return rows

    with patch.object(store, "_load_from_db", side_effect=_db):
        assert store.load_current() == rows
    assert calls["n"] == 0


def test_cache_tll_expira_y_recarga():
    store = _store_with([])
    old = [_policy(policy_id="P-vieja")]
    new = [_policy(policy_id="P-nueva")]
    with patch.object(store, "_load_from_db", return_value=old):
        store.load_current()
    # Forzar TTL vencido.
    store._cache.loaded_at = datetime.now(timezone.utc) - timedelta(
        seconds=DEFAULT_POLICY_TTL_SECONDS + 5
    )
    with patch.object(store, "_load_from_db", return_value=new) as m:
        assert store.load_current() == new
    m.assert_called_once()


def test_db_caida_con_caché_vigente_usa_stale():
    store = _store_with([])
    policies = [_policy(valid_until=datetime.now(timezone.utc) + timedelta(hours=1))]
    with patch.object(store, "_load_from_db", return_value=policies):
        store.load_current()
    # Forzar TTL vencido para que load_current intente la DB (cache aún válida
    # según su valid_until futuro).
    store._cache.loaded_at = datetime.now(timezone.utc) - timedelta(
        seconds=DEFAULT_POLICY_TTL_SECONDS + 5
    )
    with patch.object(
        store, "_load_from_db", side_effect=RuntimeError("db down")
    ):
        assert store.load_current() == policies
    assert store.db_available is False


def test_db_caida_sin_caché_raise():
    store = _store_with([])
    with patch.object(store, "_load_from_db", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            store.load_current()


def test_invalidate_fuerza_recarga():
    store = _store_with([])
    with patch.object(store, "_load_from_db", return_value=[_policy()]):
        store.load_current()
    store.invalidate()
    assert store._cache is None


# ── PolicyStore: parsing JSON de obligations/transform_spec ─────────
def test_row_to_policy_parse_json_obligations():
    row = (
        "P1", 1, "analyst", "stock_price", "READ", "TEST", None,
        GovernanceAction.PASS.value, 50, None,
        '["USE_ORIGINAL_ARGUMENTS"]', '{"fields": ["query"]}',
        datetime.now(timezone.utc), None, "sha256:x",
    )
    p = PolicyStore._row_to_policy(row)
    assert p.obligations == [Obligation.USE_ORIGINAL_ARGUMENTS]
    assert p.transform_spec == {"fields": ["query"]}


def test_row_to_policy_null_json_fields():
    row = (
        "P1", 1, "analyst", "stock_price", "READ", "TEST", None,
        GovernanceAction.PASS.value, 50, None,
        None, None, datetime.now(timezone.utc), None, "sha256:x",
    )
    p = PolicyStore._row_to_policy(row)
    assert p.obligations == []
    assert p.transform_spec is None


def test_row_to_policy_invalid_json_fail_soft():
    row = (
        "P1", 1, "analyst", "stock_price", "READ", "TEST", None,
        GovernanceAction.PASS.value, 50, None,
        "not-json", "[[[", datetime.now(timezone.utc), None, "sha256:x",
    )
    p = PolicyStore._row_to_policy(row)
    assert p.obligations == []
    assert p.transform_spec is None


# ── Unidad 4: decisión determinista ─────────────────────────────────
def test_sin_politica_coincidente_bloquea():
    evaluator = DeterministicEvaluator(settings=None, policy_source=_store_with([]))
    dec = evaluator.evaluate(_envelope())
    assert dec.action == GovernanceAction.BLOCK
    assert ReasonCode.NO_POLICY_MATCHED in dec.reason_codes


def test_bloq_prevalece_sobre_pass():
    store = _store_with([])
    store._cache = None
    policies = [
        _policy(policy_id="allow", effect=GovernanceAction.PASS),
        _policy(policy_id="deny", effect=GovernanceAction.BLOCK),
    ]
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope())
    assert dec.action == GovernanceAction.BLOCK


def test_hitl_prevalece_sobre_pass():
    store = _store_with([])
    policies = [
        _policy(policy_id="allow", effect=GovernanceAction.PASS),
        _policy(policy_id="hitl", effect=GovernanceAction.HITL),
    ]
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope())
    assert dec.action == GovernanceAction.HITL
    assert ReasonCode.HUMAN_APPROVAL_REQUIRED in dec.reason_codes


def test_obligaciones_incompatibles_producen_conflicto():
    store = _store_with([])
    policies = [
        _policy(policy_id="po", obligations=[Obligation.USE_ORIGINAL_ARGUMENTS]),
        _policy(policy_id="pt", obligations=[Obligation.USE_TRANSFORMED_ARGUMENTS]),
    ]
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope())
    assert dec.action == GovernanceAction.BLOCK
    assert ReasonCode.POLICY_CONFLICT in dec.reason_codes


def test_pass_con_obligacion_valida_sirve_decision():
    store = _store_with([])
    policies = [_policy(policy_id="allow", obligations=[Obligation.USE_ORIGINAL_ARGUMENTS])]
    with patch.object(store, "_load_from_db", return_value=policies):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope())
    assert dec.action == GovernanceAction.PASS
    assert dec.policy_id == "allow"
    assert dec.matched_policy_ids == ["allow"]


def test_tool_no_registrada_bloquea():
    store = _store_with([])
    with patch.object(store, "_load_from_db", return_value=[]):
        evaluator = DeterministicEvaluator(settings=None, policy_source=store)
        dec = evaluator.evaluate(_envelope(tool_name="unknown_tool"))
    assert dec.action == GovernanceAction.BLOCK


# ── Unidad 5: round-trip completo ───────────────────────────────────
def test_roundtrip_decition_to_dict_from_dict():
    original = PolicyDecision(
        action=GovernanceAction.PASS,
        reason_codes=[ReasonCode.ALLOWED],
        risk_level=RiskLevel.LOW,
        policy_id="P1",
        policy_version=2,
        policy_digest="sha256:abc",
        matched_policy_ids=["P1", "P2"],
        effective_priority=10,
        conflicts=[],
        obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
        transform_spec={"fields": ["query"]},
        decided_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 9, 1, 12, 5, tzinfo=timezone.utc),
    )
    data = original.to_dict()
    assert data["action"] == "PASS"
    assert data["reason_codes"] == ["ALLOWED"]
    assert data["policy_version"] == 2
    assert data["transform_spec"] == {"fields": ["query"]}

    rebuilt = PolicyDecision.from_dict(data)
    assert rebuilt.action == original.action
    assert rebuilt.reason_codes == original.reason_codes
    assert rebuilt.risk_level == original.risk_level
    assert rebuilt.policy_id == original.policy_id
    assert rebuilt.policy_version == original.policy_version
    assert rebuilt.policy_digest == original.policy_digest
    assert rebuilt.matched_policy_ids == original.matched_policy_ids
    assert rebuilt.effective_priority == original.effective_priority
    assert rebuilt.conflicts == original.conflicts
    assert rebuilt.obligations == original.obligations
    assert rebuilt.transform_spec == original.transform_spec
    assert rebuilt.decided_at == original.decided_at
    assert rebuilt.expires_at == original.expires_at