"""
E2E Fase 5 — Interfaz y evidencia académica.

Verifica end-to-end (PostgreSQL real vía red argentgob-mvp-default):
1. La vista `v_risk_evidence` existe y cruza predicción ↔ decisión final.
2. Una tool call gobernada con el modelo real deja trazabilidad completa:
   predicción persistida + decision_id vinculado + decisión final.
3. `PredictionQueryService` reporta métricas, divergencias y distribución
   temporal coherentes con lo persistido.
4. `ModelEvidenceService` recalcula la matriz de confusión y la curva de
   calibración sobre la partición de evaluación congelada.
5. INV-05: la vista no expone payload crudo ni argumentos de ejecución.

Uso:
    podman run --rm --network argentgob-mvp_default \\
        -e DATABASE_URL=... -v "${PWD}:/app:Z" -w /app \\
        localhost/argentgob-mvp-agent:latest python scripts/e2e_fase5_evidence.py
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

from argentgob.analytics.evidence import (
    ModelEvidenceService,
    PredictionQueryService,
)
from argentgob.analytics.features import FeatureBuilder
from argentgob.analytics.predictor import RiskPredictor
from argentgob.analytics.store import PredictionStore
from argentgob.core.config import Settings
from argentgob.core.envelope import AgentIdentity
from argentgob.core.decision import PolicyDecision
from argentgob.core.errors import GovernanceAction, Obligation, ReasonCode
from argentgob.db.audit import AuditWriter
from argentgob.db.connection import get_connection
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_a.spy_tool import SpyTool
from argentgob.module_c.guardrails import GuardrailEngine

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _middleware(settings, predictor, builder, store):
    """PEP con ABAC mockeado (PASS) y AuditWriter real.

    El audit real es necesario para que la vista `v_risk_evidence` pueda
    cruzar la predicción con la decisión final persistida.
    """
    decision = PolicyDecision(
        action=GovernanceAction.PASS,
        reason_code=ReasonCode.ALLOWED,
        obligations=[Obligation.USE_ORIGINAL_ARGUMENTS],
    )
    abac = MagicMock()
    abac.evaluate = MagicMock(return_value=decision)
    return GovernanceMiddleware(
        settings=settings,
        abac=abac,
        guardrails=GuardrailEngine(profile_guardrails=[], rate_limit=10),
        audit=AuditWriter(settings=settings),
        feature_builder=builder,
        risk_predictor=predictor,
        prediction_store=store,
    )


def main() -> int:
    settings = Settings(environment="TEST")
    predictor = RiskPredictor()
    builder = FeatureBuilder(policy_source=None)
    store = PredictionStore(settings)
    check("predictor disponible", predictor.available)

    # ── 1. Vista de evidencia disponible ────────────────────────────
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM v_risk_evidence")
                before = int(cur.fetchone()[0])
        finally:
            conn.close()
        check("v_risk_evidence consultable", True, f"filas={before}")
    except Exception as exc:  # noqa: BLE001
        check("v_risk_evidence consultable", False, str(exc))
        before = 0

    # ── 2. Tool call gobernada con el modelo real ───────────────────
    mw = _middleware(settings, predictor, builder, store)
    spy = SpyTool(governance=mw, agent_identity=AgentIdentity(id="e2e-f5", role="analyst"))
    spy.name = "news"
    spy.operation_class = "EXTERNAL_SEND"
    spy.resource = "duckduckgo.com"
    try:
        spy._run(query="resumen del mercado de acciones")
        check("tool call gobernada ejecutada", spy.call_count == 1)
    except Exception as exc:  # noqa: BLE001
        check("tool call gobernada ejecutada", False, str(exc))

    # ── 3. Trazabilidad: predicción vinculada a la decisión ─────────
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT prediction_id, event_id, decision_id,
                           predicted_risk_class, final_action
                    FROM v_risk_evidence
                    ORDER BY predicted_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        finally:
            conn.close()
        check("predicción visible en la vista", row is not None)
        if row:
            check("predicción vinculada a decision_id", row[2] is not None, str(row[2]))
            check(
                "decisión final presente",
                row[4] is not None,
                f"{row[3]} → {row[4]}",
            )
    except Exception as exc:  # noqa: BLE001
        check("predicción visible en la vista", False, str(exc))

    # ── 4. Servicio de consulta: métricas y divergencias ────────────
    query = PredictionQueryService()
    try:
        metrics = query.metrics()
        check("metrics.total > 0", metrics["total"] > 0, f"total={metrics['total']}")
        check(
            "distribución por clase suma el total",
            metrics["low"] + metrics["medium"] + metrics["high"] == metrics["total"],
        )
        check(
            "tasa HITL predictiva en rango",
            0.0 <= metrics["hitl_rate"] <= 1.0,
            f"{metrics['hitl_rate']}",
        )
    except Exception as exc:  # noqa: BLE001
        check("metrics.total > 0", False, str(exc))

    try:
        rows = query.list_predictions(limit=5)
        check("list_predictions devuelve filas", len(rows) > 0, f"n={len(rows)}")
        if rows:
            check(
                "cada fila trae sugerencia y divergencia",
                all("suggested_action" in r and "divergence" in r for r in rows),
            )
    except Exception as exc:  # noqa: BLE001
        check("list_predictions devuelve filas", False, str(exc))

    try:
        temporal = query.temporal_distribution(days=30)
        check("distribución temporal consultable", isinstance(temporal, list))
    except Exception as exc:  # noqa: BLE001
        check("distribución temporal consultable", False, str(exc))

    try:
        versions = query.model_versions()
        check("versiones del modelo registradas", len(versions) > 0, f"n={len(versions)}")
    except Exception as exc:  # noqa: BLE001
        check("versiones del modelo registradas", False, str(exc))

    # ── 5. Evidencia del modelo (partición congelada) ───────────────
    evidence = ModelEvidenceService()
    check("artefactos del modelo disponibles", evidence.available)
    if evidence.available:
        em = evidence.evaluation_metrics()
        check("métricas recalculadas", em.get("available") is True)
        if em.get("available"):
            cm = em["confusion_matrix"]
            check(
                "matriz de confusión 3x3 sobre 600 filas",
                len(cm) == 3 and sum(sum(r) for r in cm) == 600,
                f"n={em['n_evaluation']}",
            )
            check("macro F1 reportado", em["macro_f1"] > 0.9, str(em["macro_f1"]))
            check(
                "curva de calibración con bins",
                len(em["calibration_curve"]) > 0,
                f"bins={len(em['calibration_curve'])}",
            )

    # ── 6. INV-05: la vista no expone payload crudo ─────────────────
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'v_risk_evidence'
                    """
                )
                columns = {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
        forbidden = {"execution_arguments", "payload", "arguments", "raw_payload"}
        check(
            "vista sin columnas de payload crudo (INV-05)",
            not (columns & forbidden),
            f"columnas={len(columns)}",
        )
    except Exception as exc:  # noqa: BLE001
        check("vista sin columnas de payload crudo (INV-05)", False, str(exc))

    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n===== E2E FASE 5: {len(CHECKS) - len(failed)} passed, {len(failed)} failed =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
