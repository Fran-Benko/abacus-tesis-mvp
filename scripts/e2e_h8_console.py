"""E2E de H8 — consola sanitizada y resolución autorizada contra la DB real.

Ejercita el flujo completo contra PostgreSQL (stack local):
1. Crea una approval PENDING real (vía SQL directo, con su policy_decision).
2. ConsoleQueryService: list_decisions con filtros, metrics, pending_approvals.
3. HitlDetailService: get_detail sanitizado (sin argumentos en claro).
4. ResolutionService: aprobar con evidencia vigente (rol auditor) aplica el
   cambio; una segunda resolución (carrera) no re-resuelve; sin evidencia queda
   bloqueada.
5. Verifica que la vista v_approval_console no expone argumentos en claro.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from argentgob.console.hitl_detail import HitlDetailService
from argentgob.console.resolution import ResolutionService
from argentgob.console.service import ConsoleQueryService
from argentgob.core.config import Settings
from argentgob.db.connection import get_connection
from argentgob.identity.provider import AuditorIdentityProvider
from argentgob.identity.roles import AppRole
from argentgob.hitl.resolver import ApprovalResolver

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def main():
    settings = Settings(
        environment="LOCAL",
        authorized_auditor_roles=["auditor"],
        identity_development_only=True,
        identity_session_ttl_seconds=3600,
        identity_fixture_roles={"auditor-1": ["viewer", "auditor"]},
    )

    # ── 1. Crear una approval PENDING real ────────────────────────────
    now = datetime.now(timezone.utc)
    decision_id = f"dec-{uuid.uuid4().hex[:12]}"
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    approval_id = f"apr-{uuid.uuid4().hex[:12]}"
    digest = f"sha256:{uuid.uuid4().hex}"

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO policy_decisions (
                            decision_id, event_id, agent_id, tool_name, operation_class,
                            resource, environment, action, risk_level, reason_codes,
                            policy_id, policy_version, policy_digest, matched_policy_ids,
                            effective_priority, conflicts, obligations, transform_spec,
                            decided_at, expires_at
                        ) VALUES (
                            %(decision_id)s, %(event_id)s, 'agent-1', 'stock_price',
                            'read', 'ticker', 'LOCAL', 'HITL', 'HIGH',
                            '["requires_human_approval"]', 'pol-001', 1, %(digest)s,
                            '["pol-001"]', 1, '[]', '[]', '{}', %(now)s, %(exp)s
                        )
            """,
            {
                "decision_id": decision_id,
                "event_id": event_id,
                "digest": digest,
                "now": now,
                "exp": now + timedelta(seconds=300),
            },
        )
        cur.execute(
            """
            INSERT INTO approval_requests (
                approval_id, event_id, decision_id, agent_id, tool_name,
                argument_digest, idempotency_key, state, argued_at, expires_at
            ) VALUES (
                %(approval_id)s, %(event_id)s, %(decision_id)s, 'agent-1',
                'stock_price', %(digest)s, %(idem)s, 'PENDING', %(now)s, %(exp)s
            )
            """,
            {
                "approval_id": approval_id,
                "event_id": event_id,
                "decision_id": decision_id,
                "digest": digest,
                "idem": f"idem-{uuid.uuid4().hex[:12]}",
                "now": now,
                "exp": now + timedelta(seconds=300),
            },
        )
    conn.close()
    print(f"Created approval {approval_id} (decision {decision_id})")

    # ── 2. ConsoleQueryService ────────────────────────────────────────
    print("\n== ConsoleQueryService ==")
    svc = ConsoleQueryService()
    metrics = svc.metrics()
    check("metrics.total >= 1", metrics["total"] >= 1, str(metrics))
    check("metrics.hitl >= 1", metrics["hitl"] >= 1, str(metrics))

    decisions = svc.list_decisions(action="HITL", limit=10)
    check("list_decisions(action=HITL) returns rows", len(decisions) >= 1)
    if decisions:
        d = decisions[0]
        check("decision has no execution_arguments", "execution_arguments" not in d)
        check("decision has no raw payload", "payload" not in d)

    pending = svc.pending_approvals(limit=10)
    check("pending_approvals returns the new approval",
          any(p["approval_id"] == approval_id for p in pending))

    # ── 3. HitlDetailService ──────────────────────────────────────────
    print("\n== HitlDetailService ==")
    detail_svc = HitlDetailService()
    detail = detail_svc.get_detail(approval_id=approval_id)
    check("detail.state == PENDING", detail.get("state") == "PENDING", str(detail.get("state")))
    check("detail.tool_name == stock_price", detail.get("tool_name") == "stock_price")
    check("detail.risk_level == HIGH", detail.get("risk_level") == "HIGH")
    check("detail has argument_digest (sanitized preview)",
          detail.get("argument_digest") == digest)
    check("detail has NO execution_arguments", "execution_arguments" not in detail)
    check("detail has NO raw argument", "argument" not in detail or detail.get("argument") is None)
    check("detail has age_seconds", detail.get("age_seconds") is not None)
    check("detail has expires_in_seconds", detail.get("expires_in_seconds") is not None)

    # ── 4. ResolutionService ──────────────────────────────────────────
    print("\n== ResolutionService ==")
    provider = AuditorIdentityProvider(
        environment="LOCAL",
        development_only=True,
        session_ttl_seconds=3600,
        fixture_roles={"auditor-1": ["viewer", "auditor"]},
    )
    resolver = ApprovalResolver(settings=settings)
    res_svc = ResolutionService(
        identity_provider=provider,
        resolver=resolver,
        detail_service=detail_svc,
    )

    # 4a. Sin evidencia -> blocked (no autorizado).
    blocked = res_svc.resolve(approval_id=approval_id, decision="APPROVED", evidence=None)
    check("resolve without evidence -> blocked",
          blocked.get("outcome") == "blocked", str(blocked))

    # 4b. Evidencia vigente + rol auditor -> approved.
    evidence = provider.authenticate(identifier="auditor-1", role=AppRole.AUDITOR)
    approved = res_svc.resolve(
        approval_id=approval_id, decision="APPROVED", evidence=evidence, comment="ok"
    )
    check("resolve with valid evidence -> approved",
          approved.get("outcome") == "approved", str(approved))

    # 4c. Segunda resolución (carrera) -> already_resolved.
    again = res_svc.resolve(
        approval_id=approval_id, decision="APPROVED", evidence=evidence
    )
    check("second resolve -> already_resolved",
          again.get("outcome") == "already_resolved", str(again))

    # 4d. Estado en DB.
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT state, resolved_by, decision FROM approval_requests "
            "WHERE approval_id = %(approval_id)s",
            {"approval_id": approval_id},
        )
        row = cur.fetchone()
    conn.close()
    check("DB state == APPROVED", row[0] == "APPROVED", str(row))
    check("DB resolved_by == auditor-1", row[1] == "auditor-1", str(row))
    check("DB decision == APPROVED", row[2] == "APPROVED", str(row))

    # ── 5. Vista v_approval_console no expone argumentos ──────────────
    print("\n== v_approval_console sanitized ==")
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM v_approval_console WHERE approval_id = %(approval_id)s",
            {"approval_id": approval_id},
        )
        cols = [d.name for d in cur.description]
        vrow = cur.fetchone()
    conn.close()
    check("view has no execution_arguments column", "execution_arguments" not in cols)
    check("view has no raw argument column", "argument" not in cols)
    check("view row present", vrow is not None)

    # ── Limpieza ──────────────────────────────────────────────────────
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM approval_requests WHERE approval_id = %(a)s",
                    {"a": approval_id})
        cur.execute("DELETE FROM policy_decisions WHERE decision_id = %(d)s",
                    {"d": decision_id})
    conn.close()
    print(f"\nCleaned up {approval_id} / {decision_id}")

    print(f"\n===== E2E H8: {PASS} passed, {FAIL} failed =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())