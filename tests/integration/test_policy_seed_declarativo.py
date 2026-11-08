"""Integración: seed de políticas declarativas (YAML) contra el store en memoria.

Verifica que `scripts.seed_policies.seed()` con `connection_factory` inyectada
persiste las políticas de `policies/*.yaml` (fuente canónica) y que el
`PolicyStore` puede leerlas de vuelta como `GovernancePolicy` (mismo contrato
que en producción).
"""
from __future__ import annotations

from scripts import seed_policies as seed_module
from tests.integration._mem_db import MemConn, _new_store


class _MemDb:
    """Fabrica de conexiones en memoria: comparte el mismo store."""

    def __init__(self):
        self.store = _new_store()

    def factory(self):
        return MemConn(self.store)


def test_seed_desde_yaml_persiste_y_policy_store_lee():
    db = _MemDb()
    inserted = seed_module.seed(environment="TEST", connection_factory=db.factory)

    # 6 políticas declarativas -> admin expande 1 a 3 = 8 filas.
    assert inserted == 8

    from argentgob.module_c.policy_store import PolicyStore

    store = PolicyStore.__new__(PolicyStore)
    store.settings = None
    store.ttl_seconds = 30
    store._cache = None
    store._lock = __import__("threading").Lock()
    store._db_available = True
    from unittest.mock import patch

    rows = [
        dict(r)
        for r in db.store["governance_policies"].values()
    ]
    with patch.object(store, "_load_from_db", return_value=[], create=True):
        # Reconstruimos las filas como GovernancePolicy vía _row_to_policy.
        policies = [
            PolicyStore._row_to_policy(
                (
                        r["policy_id"], r["policy_version"], r["profile"],
                        r["tool"], r["op_class"], r["environment"],
                    r["resource"], r["effect"], r["priority"],
                    r["sensitivity_limit"], r["obligations"], r["transform_spec"],
                    r["valid_from"], r["valid_until"], r["policy_digest"],
                )
            )
            for r in rows
        ]
    assert len(policies) == 8
    ids = {p.policy_id for p in policies}
    assert "POL-admin-001-NEWS" in ids
    assert "POL-admin-001-STOCK_PRICE" in ids
    assert "POL-admin-001-CRYPTO_PRICE" in ids
    assert "POL-analyst-001" in ids

    # El analyst-001 declara sensitivity_limit MEDIUM (ejercido por evaluador).
    news_policy = next(p for p in policies if p.policy_id == "POL-analyst-001")
    assert news_policy.sensitivity_limit == "MEDIUM"
    assert news_policy.obligations


def test_seed_upsert_es_idempotente():
    db = _MemDb()
    first = seed_module.seed(environment="TEST", connection_factory=db.factory)
    second = seed_module.seed(environment="TEST", connection_factory=db.factory)
    assert first == second == 8
    # No duplica: el store guarda por (policy_id, policy_version).
    assert len(db.store["governance_policies"]) == 8


def test_seed_prune_elimina_politicas_huerfanas():
    """El seed borra políticas del environment no declaradas en YAML."""
    db = _MemDb()
    seed_module.seed(environment="TEST", connection_factory=db.factory)

    # Huérfana: existe en DB pero no en los YAML.
    db.store["governance_policies"][("POL-huerfana", 1)] = {
        "policy_id": "POL-huerfana",
        "policy_version": 1,
        "profile": "analyst",
        "tool": "news",
        "op_class": "EXTERNAL_SEND",
        "environment": "TEST",
        "resource": None,
        "effect": "PASS",
        "priority": 50,
        "sensitivity_limit": None,
        "obligations": '["USE_ORIGINAL_ARGUMENTS"]',
        "transform_spec": None,
        "valid_from": None,
        "valid_until": None,
        "policy_digest": None,
    }
    seed_module.seed(environment="TEST", connection_factory=db.factory)
    assert ("POL-huerfana", 1) not in db.store["governance_policies"]
    assert len(db.store["governance_policies"]) == 8