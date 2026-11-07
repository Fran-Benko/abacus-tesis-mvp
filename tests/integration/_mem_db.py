"""Conexion PostgreSQL en memoria para tests de integracion de flujo completo.

Implementa un subconjunto del contrato psycopg suficiente para ejecutar los
servicios reales del flujo de gobernanza HITL end-to-end sin una base de datos
real: `governance_decisions`, `governance_events`, `risk_predictions`,
`approval_requests`, `approval_resume_tokens`, `execution_results`,
`policy_decisions`, `audit_chain`, `model_drift_reports` y las vistas
`v_governance_console`, `v_approval_console`, `v_risk_evidence`.

Los servicios usan `connection_factory` inyectable, por lo que basta con
proporcionar una fabrica que devuelva esta conexion en memoria. Cada conexion
comparte el mismo `store` (dict de tablas), de modo que las distintas llamadas
(AuditWriter, ApprovalService, GovernedResumeService, ExecutionOrchestrator,
HitlDetailService, ConsoleQueryService, PredictionQueryService) ven el mismo
estado, como con una DB real.

INV-05: nunca se persiste el payload crudo; solo digests.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_store() -> dict[str, dict[str, dict[str, Any]]]:
    """Crea un store vacio: por tabla, un dict por PK."""
    return {
        "governance_decisions": {},
        "governance_events": {},
        "risk_predictions": {},
        "approval_requests": {},
        "approval_resume_tokens": {},
        "execution_results": {},
        "policy_decisions": {},
        "audit_chain": {},
        "model_drift_reports": {},
    }


class MemCursor:
    """Cursor en memoria con el contrato de psycopg usado por los servicios."""

    def __init__(self, conn: MemConn):
        self.conn = conn
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: dict[str, Any] | None = None):
        self.conn.record.append((sql, params))
        self.conn._last_sql = sql
        self.conn._last_params = params or {}
        with self.conn._lock:
            rowcount, rows = self.conn.dispatch(sql, params or {})
        self.rowcount = rowcount
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class MemConn:
    """Conexion en memoria compartida por todos los servicios del flujo."""

    def __init__(self, store: dict[str, dict[str, dict[str, Any]]]):
        self.autocommit = True
        self.store = store
        self.record: list[tuple[str, dict]] = []
        self._last_sql = ""
        self._last_params: dict[str, Any] = {}
        self._seq = 0
        self._lock = threading.RLock()
        self.closed = False

    def cursor(self, row_factory=None):
        return MemCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    # -- Despacho de consultas ---------------------------------------
    def dispatch(
        self, sql: str, params: dict[str, Any]
    ) -> tuple[int, list[dict[str, Any]]]:
        """Clasifica el SQL por la tabla/vista y delega en el handler."""
        upper = " ".join(sql.split()).upper()
        if "INSERT INTO GOVERNANCE_DECISIONS" in upper:
            return self._insert_governance_decisions(params)
        if "INSERT INTO GOVERNANCE_EVENTS" in upper:
            return self._insert_governance_events(params)
        if "INSERT INTO RISK_PREDICTIONS" in upper:
            return self._insert_risk_predictions(params)
        if "INSERT INTO APPROVAL_REQUESTS" in upper:
                    if "ON CONFLICT (IDEMPOTENCY_KEY)" in upper:
                        return self._insert_approval_request_idempotent(params)
                    return self._insert_approval_request(params)
        if "INSERT INTO APPROVAL_RESUME_TOKENS" in upper:
            return self._insert_approval_token(params)
        if "INSERT INTO EXECUTION_RESULTS" in upper:
            return self._insert_execution_result(params)
        if "INSERT INTO POLICY_DECISIONS" in upper:
            return self._insert_policy_decision(params)
        if "INSERT INTO AUDIT_CHAIN" in upper:
            return self._insert_audit_chain(params)
        if "INSERT INTO MODEL_DRIFT_REPORTS" in upper:
            return self._insert_model_drift_report(params)
        if "FROM MODEL_DRIFT_REPORTS" in upper:
            return self._select_model_drift_reports(sql, params)
        if "UPDATE APPROVAL_REQUESTS" in upper:
            return self._update_approval_request(params)
        if "UPDATE APPROVAL_RESUME_TOKENS" in upper:
            return self._update_approval_token(params)
        if "FROM APPROVAL_RESUME_TOKENS" in upper:
            return self._select_approval_token(params)
        if "FROM APPROVAL_REQUESTS AR LEFT JOIN POLICY_DECISIONS" in upper:
            return self._select_approval_detail(params)
        if "FROM APPROVAL_REQUESTS" in upper:
            return self._select_approval_requests(sql, params)
        if "FROM V_APPROVAL_CONSOLE" in upper:
            return self._select_approval_console(sql, params)
        if "FROM POLICY_DECISIONS" in upper:
            return self._select_policy_decisions(sql, params)
        if "FROM V_GOVERNANCE_CONSOLE" in upper:
            return self._select_governance_console(sql, params)
        if "FROM V_RISK_EVIDENCE" in upper:
            return self._select_risk_evidence(sql, params)
        if "FROM AUDIT_CHAIN" in upper:
            return self._select_audit_chain(params)
        # Fallback: nada conocido -> sin filas.
        return (0, [])

    # -- Inserts ------------------------------------------------------
    def _insert_governance_decisions(self, params):
        d = {
            "decision_id": params.get("decision_id"),
            "event_id": params.get("event_id"),
            "agent_id": params.get("agent_id"),
            "agent_role": params.get("agent_role"),
            "tool_name": params.get("tool_name"),
            "operation_class": params.get("operation_class"),
            "action": params.get("action"),
            "reason_code": params.get("reason_code"),
            "policy_id": params.get("policy_id"),
            "payload_digest": params.get("payload_digest"),
            "blocked_by_guardrail": params.get("blocked_by_guardrail"),
            "latency_pre_ms": params.get("latency_pre_ms"),
            "decided_at": params.get("decided_at") or _utcnow(),
        }
        self.store["governance_decisions"][d["decision_id"]] = d
        return (1, [])

    def _insert_governance_events(self, params):
        e = {
            "event_id": params.get("event_id"),
            "decision_id": params.get("decision_id") or "",
            "tool_name": params.get("tool_name"),
            "status": params.get("status"),
            "error_message": params.get("error_message"),
            "latency_post_ms": params.get("latency_post_ms"),
            "recorded_at": _utcnow(),
        }
        self.store["governance_events"][e["event_id"]] = e
        return (1, [])

    def _insert_risk_predictions(self, params):
        p = {
            "prediction_id": params.get("prediction_id"),
            "event_id": params.get("event_id"),
            "decision_id": params.get("decision_id"),
            "model_id": params.get("model_id"),
            "model_version": params.get("model_version"),
            "predicted_risk_class": params.get("predicted_risk_class"),
            "risk_probability": params.get("risk_probability"),
            "class_probabilities": params.get("class_probabilities"),
            "inference_latency_ms": params.get("inference_latency_ms"),
            "feature_schema_version": params.get("feature_schema_version"),
            "predicted_at": _utcnow(),
        }
        existing = self.store["risk_predictions"].get(p["prediction_id"])
        if existing is not None and params.get("decision_id") is not None:
            existing["decision_id"] = params["decision_id"]
        else:
            self.store["risk_predictions"][p["prediction_id"]] = p
        return (1, [])

    def _insert_approval_request(self, params):
        a = {
            "approval_id": params.get("approval_id"),
            "event_id": params.get("event_id"),
            "decision_id": params.get("decision_id"),
            "agent_id": params.get("agent_id"),
            "tool_name": params.get("tool_name"),
            "argument_digest": params.get("argument_digest"),
            "idempotency_key": params.get("idempotency_key"),
            "state": "PENDING",
            "argued_at": params.get("argued_at") or _utcnow(),
            "expires_at": params.get("expires_at"),
            "resolved_at": None,
            "resolved_by": None,
            "decision": None,
        }
        self.store["approval_requests"][a["approval_id"]] = a
        return (1, [a])

    def _insert_approval_request_idempotent(self, params):
        """ON CONFLICT (idempotency_key): si existe, devuelve la fila existente.

        Reproduce la semantica de ApprovalService: misma idempotency_key ->
        misma approval (no se duplica).
        """
        for existing in self.store["approval_requests"].values():
            if existing["idempotency_key"] == params.get("idempotency_key"):
                return (1, [dict(existing)])
        return self._insert_approval_request(params)

    def _insert_approval_token(self, params):
        t = {
            "token_id": params.get("token_id"),
            "approval_id": params.get("approval_id"),
            "token_hash": params.get("token_hash"),
            "state": "ACTIVE",
            "created_at": _utcnow(),
            "expires_at": params.get("expires_at"),
            "redeemed_at": None,
        }
        self.store["approval_resume_tokens"][t["token_id"]] = t
        return (1, [])

    def _insert_execution_result(self, params):
        r = {
            "event_id": params.get("event_id"),
            "decision_id": params.get("decision_id"),
            "tool_name": params.get("tool_name"),
            "status": params.get("status"),
            "error_digest": params.get("error_digest"),
            "result_digest": params.get("result_digest"),
            "latency_post_ms": params.get("latency_post_ms"),
            "recorded_at": _utcnow(),
        }
        existing = self.store["execution_results"].get(r["event_id"])
        if existing is None:
            self.store["execution_results"][r["event_id"]] = r
        return (1, [])

    def _insert_policy_decision(self, params):
        d = {
            "decision_id": params.get("decision_id"),
            "event_id": params.get("event_id"),
            "session_id": params.get("session_id"),
            "agent_id": params.get("agent_id"),
            "agent_role": params.get("agent_role"),
            "tool_name": params.get("tool_name"),
            "operation_class": params.get("operation_class"),
            "resource": params.get("resource"),
            "environment": params.get("environment"),
            "payload_digest": params.get("payload_digest"),
            "action": params.get("action"),
            "risk_level": params.get("risk_level"),
            "reason_codes": params.get("reason_codes") or [],
            "policy_id": params.get("policy_id"),
            "policy_version": params.get("policy_version"),
            "policy_digest": params.get("policy_digest"),
            "matched_policy_ids": params.get("matched_policy_ids") or [],
            "effective_priority": params.get("effective_priority"),
            "conflicts": params.get("conflicts") or [],
            "obligations": params.get("obligations") or [],
            "transform_spec": params.get("transform_spec") or {},
            "decided_at": params.get("decided_at") or _utcnow(),
            "expires_at": params.get("expires_at"),
            "blocked_by_guardrail": params.get("blocked_by_guardrail"),
            "latency_pre_ms": params.get("latency_pre_ms"),
        }
        self.store["policy_decisions"][d["decision_id"]] = d
        return (1, [])

    def _insert_audit_chain(self, params):
        self._seq += 1
        a = {
            "sequence": self._seq,
            "event_id": params.get("event_id"),
            "decision_id": params.get("decision_id"),
            "action": params.get("action"),
            "chain_hash": params.get("chain_hash"),
            "prev_hash": params.get("prev_hash"),
            "payload": params.get("payload"),
            "recorded_at": _utcnow(),
        }
        self.store["audit_chain"][str(self._seq)] = a
        return (1, [])

    # -- Updates ------------------------------------------------------
    def _update_approval_request(self, params):
        """Aplica UPDATE condicional sobre approval_requests.

        Dos casos:
        - Resolucion (ApprovalResolver): `SET state=%(decision)s ... WHERE
          state='PENDING' AND argument_digest=%(digest)s AND no vencida`.
        - Consumo de token (GovernedResumeService): `SET state='APPROVED'
          WHERE approval_id=%(approval_id)s AND state='APPROVED'` (no-op).
        """
        decision = params.get("decision")
        approval_id = params.get("approval_id")
        if decision is None and params.get("state") is None:
            return (0, [])
        rows: list[dict[str, Any]] = []
        rowcount = 0
        now = _utcnow()
        for a in self.store["approval_requests"].values():
            if approval_id and a["approval_id"] != approval_id:
                continue
            new_state = decision or params.get("state")
            if decision is None:
                if a["state"] != "APPROVED":
                    continue
                a["state"] = "APPROVED"
                rows.append(a)
                rowcount += 1
                continue
            if a["state"] != "PENDING":
                continue
            if params.get("digest") and a["argument_digest"] != params["digest"]:
                continue
            if params.get("expires_at") and a["expires_at"] and a["expires_at"] <= now:
                continue
            a["state"] = new_state
            a["resolved_at"] = params.get("resolved_at") or now
            a["resolved_by"] = params.get("resolved_by")
            a["decision"] = new_state
            rows.append(a)
            rowcount += 1
        return (rowcount, rows)

    def _update_approval_token(self, params):
        token_id = params.get("token_id")
        for t in self.store["approval_resume_tokens"].values():
            if t["token_id"] != token_id:
                continue
            if t["state"] != "ACTIVE":
                continue
            t["state"] = "REDEEMED"
            t["redeemed_at"] = params.get("now") or _utcnow()
            return (1, [t])
        return (0, [])

    # -- Selects ------------------------------------------------------
    def _select_approval_token(self, params):
        token_id = params.get("token_id")
        t = self.store["approval_resume_tokens"].get(token_id)
        if t is None:
            return (0, [])
        # Tupla posicional (orden del SELECT en GovernedResumeService):
        # token_id, approval_id, token_hash, state, created_at, expires_at, redeemed_at
        return (
            0,
            [
                (
                    t["token_id"],
                    t["approval_id"],
                    t["token_hash"],
                    t["state"],
                    t["created_at"],
                    t["expires_at"],
                    t["redeemed_at"],
                )
            ],
        )

    def _select_approval_requests(self, sql, params):
        rows = list(self.store["approval_requests"].values())
        upper = " ".join(sql.split()).upper()
        if params.get("approval_id") and "FILTER" not in upper and "PENDING" not in upper:
            rows = [r for r in rows if r["approval_id"] == params["approval_id"]]
        if rows and "STATE = 'PENDING'" not in upper:
            # Tupla posicional (orden del SELECT de _load_approval):
            # approval_id, event_id, decision_id, agent_id, tool_name,
            # argument_digest, idempotency_key, state, argued_at, expires_at,
            # resolved_at, resolved_by, decision
            return (
                0,
                [
                    (
                        r["approval_id"],
                        r["event_id"],
                        r["decision_id"],
                        r["agent_id"],
                        r["tool_name"],
                        r["argument_digest"],
                        r["idempotency_key"],
                        r["state"],
                        r["argued_at"],
                        r["expires_at"],
                        r["resolved_at"],
                        r["resolved_by"],
                        r["decision"],
                    )
                    for r in rows
                ],
            )
        if "STATE = 'PENDING'" in upper:
            rows = [r for r in rows if r["state"] == "PENDING"]
        if "EXPIRES_AT <= NOW()" in upper:
            rows = [
                r
                for r in rows
                if r["expires_at"] is not None and r["expires_at"] <= _utcnow()
            ]
        if "ORDER BY ARGUED_AT ASC" in upper:
            rows = sorted(rows, key=lambda r: r["argued_at"] or _utcnow())
        if "ORDER BY EXPIRES_AT ASC" in upper:
            rows = sorted(rows, key=lambda r: r["expires_at"] or _utcnow())
        if params.get("limit"):
            rows = rows[: int(params["limit"])]
        return (0, [dict(r) for r in rows])

    def _select_approval_detail(self, params):
        """HitlDetailService: join approval_requests con policy_decisions.

        Devuelve el dict con las columnas del SELECT de hitl_detail (acceso por
        clave via dict_row). Sin execution_arguments ni payload crudo (INV-05).
        """
        a = None
        for cand in self.store["approval_requests"].values():
            if cand["approval_id"] == params.get("approval_id"):
                a = cand
                break
        if a is None:
            return (0, [])
        d = self.store["policy_decisions"].get(a.get("decision_id") or "")
        return (
            0,
            [
                {
                    "approval_id": a["approval_id"],
                    "event_id": a["event_id"],
                    "decision_id": a["decision_id"],
                    "agent_id": a["agent_id"],
                    "tool_name": a["tool_name"],
                    "argument_digest": a["argument_digest"],
                    "idempotency_key": a["idempotency_key"],
                    "state": a["state"],
                    "argued_at": a["argued_at"],
                    "expires_at": a["expires_at"],
                    "resolved_at": a["resolved_at"],
                    "resolved_by": a["resolved_by"],
                    "decision": a["decision"],
                    "resource": d.get("resource") if d else None,
                    "environment": d.get("environment") if d else None,
                    "risk_level": d.get("risk_level") if d else None,
                    "reason_codes": d.get("reason_codes") if d else None,
                    "policy_id": d.get("policy_id") if d else None,
                    "policy_version": d.get("policy_version") if d else None,
                    "operation_class": d.get("operation_class") if d else None,
                }
            ],
        )

    def _select_approval_console(self, sql, params):
        rows = []
        for a in self.store["approval_requests"].values():
            d = self.store["policy_decisions"].get(a.get("decision_id") or "")
            rows.append(
                {
                    "approval_id": a["approval_id"],
                    "event_id": a["event_id"],
                    "decision_id": a["decision_id"],
                    "agent_id": a["agent_id"],
                    "tool_name": a["tool_name"],
                    "argument_digest": a["argument_digest"],
                    "idempotency_key": a["idempotency_key"],
                    "state": a["state"],
                    "argued_at": a["argued_at"],
                    "expires_at": a["expires_at"],
                    "resolved_at": a["resolved_at"],
                    "resolved_by": a["resolved_by"],
                    "decision": a["decision"],
                    "risk_level": d.get("risk_level") if d else None,
                    "reason_codes": d.get("reason_codes") if d else None,
                    "policy_id": d.get("policy_id") if d else None,
                    "policy_version": d.get("policy_version") if d else None,
                    "operation_class": d.get("operation_class") if d else None,
                    "resource": d.get("resource") if d else None,
                    "environment": d.get("environment") if d else None,
                }
            )
        if params.get("approval_id"):
            rows = [r for r in rows if r["approval_id"] == params["approval_id"]]
        if params.get("state"):
            rows = [r for r in rows if r["state"] == params["state"]]
        return (0, rows)

    def _select_policy_decisions(self, sql, params):
        rows = list(self.store["policy_decisions"].values())
        if params.get("event_id"):
            rows = [r for r in rows if r["event_id"] == params["event_id"]]
        if params.get("decision_id"):
            rows = [r for r in rows if r["decision_id"] == params["decision_id"]]
        return (0, rows)

    def _select_governance_console(self, sql, params):
        rows = self._console_rows()
        upper = " ".join(sql.split()).upper()
        if "COUNT(*) FILTER" in upper:
            total = len(rows)
            return (
                0,
                [
                    {
                        "total_pass": sum(1 for r in rows if r["action"] == "PASS"),
                        "total_block": sum(1 for r in rows if r["action"] == "BLOCK"),
                        "total_hitl": sum(1 for r in rows if r["action"] == "HITL"),
                        "total": total,
                        "total_failed": sum(1 for r in rows if r["status"] == "FAILED"),
                        "total_unknown": sum(1 for r in rows if r["status"] == "UNKNOWN"),
                        "total_no_result": sum(1 for r in rows if r["status"] is None),
                    }
                ],
            )
        if "PERCENTILE_CONT" in upper:
            lat = sorted(
                r["latency_post_ms"]
                for r in rows
                if r["latency_post_ms"] is not None
            )
            if not lat:
                return (0, [{"p50": None, "p90": None, "p99": None}])
            n = len(lat)
            p50 = lat[min(int(0.50 * (n - 1)), n - 1)]
            p90 = lat[min(int(0.90 * (n - 1)), n - 1)]
            p99 = lat[min(int(0.99 * (n - 1)), n - 1)]
            return (0, [{"p50": p50, "p90": p90, "p99": p99}])
        if params.get("action"):
            rows = [r for r in rows if r["action"] == params["action"]]
        if params.get("tool_name"):
            rows = [r for r in rows if r["tool_name"] == params["tool_name"]]
        if params.get("risk_level"):
            rows = [r for r in rows if r["risk_level"] == params["risk_level"]]
        if params.get("status"):
            rows = [r for r in rows if r["status"] == params["status"]]
        if params.get("limit"):
            rows = rows[: int(params["limit"])]
        if params.get("offset"):
            rows = rows[int(params["offset"]):]
        rows = sorted(rows, key=lambda r: r["decided_at"] or _utcnow(), reverse=True)
        return (0, rows)

    def _console_rows(self) -> list[dict[str, Any]]:
        rows = []
        for d in self.store["governance_decisions"].values():
            ev = self.store["governance_events"].get(d["event_id"])
            pd = self.store["policy_decisions"].get(d["decision_id"])
            rows.append(
                {
                    "decision_id": d["decision_id"],
                    "event_id": d["event_id"],
                    "agent_id": d["agent_id"],
                    "agent_role": d["agent_role"],
                    "tool_name": d["tool_name"],
                    "operation_class": d["operation_class"],
                    "resource": pd.get("resource") if pd else None,
                    "environment": pd.get("environment") if pd else None,
                    "payload_digest": d["payload_digest"],
                    "action": d["action"],
                    "risk_level": pd.get("risk_level") if pd else "LOW",
                    "reason_codes": pd.get("reason_codes") if pd else [d["reason_code"]],
                    "policy_id": d["policy_id"],
                    "policy_version": pd.get("policy_version") if pd else None,
                    "effective_priority": pd.get("effective_priority") if pd else None,
                    "conflicts": pd.get("conflicts") if pd else [],
                    "obligations": pd.get("obligations") if pd else [],
                    "decided_at": d["decided_at"],
                    "expires_at": pd.get("expires_at") if pd else None,
                    "status": ev["status"] if ev else None,
                    "error_digest": None,
                    "result_digest": None,
                    "latency_post_ms": ev["latency_post_ms"] if ev else None,
                    "recorded_at": ev["recorded_at"] if ev else None,
                }
            )
        return rows

    def _select_risk_evidence(self, sql, params):
        rows = self._evidence_rows()
        upper = " ".join(sql.split()).upper()
        if "COUNT(*) FILTER" in upper:
            total = len(rows)
            probs = [
                r["risk_probability"] for r in rows if r["risk_probability"] is not None
            ]
            lat = [
                r["inference_latency_ms"]
                for r in rows
                if r["inference_latency_ms"] is not None
            ]
            return (
                0,
                [
                    {
                        "total": total,
                        "low": sum(1 for r in rows if r["predicted_risk_class"] == "LOW"),
                        "medium": sum(
                            1 for r in rows if r["predicted_risk_class"] == "MEDIUM"
                        ),
                        "high": sum(1 for r in rows if r["predicted_risk_class"] == "HIGH"),
                        "without_decision": sum(1 for r in rows if r["decision_id"] is None),
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
                ],
            )
        if params.get("predicted_class"):
            rows = [
                r
                for r in rows
                if r["predicted_risk_class"] == params["predicted_class"]
            ]
        if params.get("final_action"):
            rows = [r for r in rows if r["final_action"] == params["final_action"]]
        if params.get("limit"):
            rows = rows[: int(params["limit"])]
        if params.get("offset"):
            rows = rows[int(params["offset"]):]
        rows = sorted(rows, key=lambda r: r["predicted_at"] or _utcnow(), reverse=True)
        return (0, rows)

    def _evidence_rows(self) -> list[dict[str, Any]]:
        rows = []
        for p in self.store["risk_predictions"].values():
            d = self.store["governance_decisions"].get(p.get("decision_id") or "")
            rows.append(
                {
                    "prediction_id": p["prediction_id"],
                    "event_id": p["event_id"],
                    "decision_id": p.get("decision_id"),
                    "model_id": p.get("model_id"),
                    "model_version": p.get("model_version"),
                    "predicted_risk_class": p.get("predicted_risk_class"),
                    "risk_probability": p.get("risk_probability"),
                    "class_probabilities": p.get("class_probabilities"),
                    "inference_latency_ms": p.get("inference_latency_ms"),
                    "feature_schema_version": p.get("feature_schema_version"),
                    "predicted_at": p.get("predicted_at"),
                    "final_action": d["action"] if d else None,
                    "final_reason_code": d["reason_code"] if d else None,
                    "tool_name": d["tool_name"] if d else None,
                    "agent_role": d["agent_role"] if d else None,
                    "operation_class": d["operation_class"] if d else None,
                    "decided_at": d["decided_at"] if d else None,
                }
            )
        return rows

    def _select_audit_chain(self, params):
        rows = sorted(
            self.store["audit_chain"].values(),
            key=lambda r: r["sequence"],
        )
        return (0, rows)

    def _insert_model_drift_report(self, params):
        import json

        r = {
            "report_id": params.get("report_id"),
            "model_id": params.get("model_id"),
            "model_version": params.get("model_version"),
            "feature_schema_version": params.get("feature_schema_version"),
            "generated_at": _utcnow(),
            "class_psi": params.get("class_psi"),
            "feature_psi": params.get("feature_psi"),
            "class_distribution_baseline": params.get("class_distribution_baseline"),
            "class_distribution_observed": params.get(
                "class_distribution_observed"
            ),
            "severity_class": params.get("severity_class"),
            "severity_global": params.get("severity_global"),
            "sample_size_baseline": params.get("sample_size_baseline"),
            "sample_size_observed": params.get("sample_size_observed"),
            "features_compared": params.get("features_compared"),
        }
        # Los JSON llegan como strings desde el store; los normalizamos.
        for key in (
            "feature_psi",
            "class_distribution_baseline",
            "class_distribution_observed",
        ):
            val = r.get(key)
            if isinstance(val, str):
                try:
                    r[key] = json.loads(val)
                except (TypeError, ValueError):
                    r[key] = {}
        self.store["model_drift_reports"][r["report_id"]] = r
        return (1, [])

    def _select_model_drift_reports(self, sql, params):
        rows = [
            dict(r)
            for r in sorted(
                self.store["model_drift_reports"].values(),
                key=lambda r: r["generated_at"] or _utcnow(),
                reverse=True,
            )
        ]
        if params.get("limit"):
            rows = rows[: int(params["limit"])]
        return (0, rows)
