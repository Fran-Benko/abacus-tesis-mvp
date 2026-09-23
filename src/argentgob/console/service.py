"""H8 â€” ConsoleQueryService: consultas sanitizadas de la consola (Unidad 1).

Lee exclusivamente de la vista `v_governance_console` (sanitizada, sin raw
payload) y de `approval_requests` (solo campos autorizados). Todos los filtros
son parametrizados (sin interpolaciÃ³n de SQL). NUNCA expone
`execution_arguments` ni el payload crudo (INV-05).

MÃ©tricas:
- Conteos PASS/BLOCK/HITL y total.
- Percentiles de latencia post-hook (p50/p90/p99).
- Errores (execution_results con status FAILED/UNKNOWN).
- Approvals PENDING y vencidas.
- Estado de la cadena de auditorÃ­a (Ã­ntegra / motivo sanitizado).
"""
from typing import Any, Callable

from psycopg.rows import dict_row

from argentgob.audit.verify import AuditChainVerifier
from argentgob.db.connection import get_connection
from argentgob.observability.logger import get_logger


log = get_logger(__name__)


class ConsoleQueryService:
    """Consultas sanitizadas y parametrizadas sobre la vista de gobernanza."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
        verifier: AuditChainVerifier | None = None,
    ):
        self._connection_factory = connection_factory or (lambda: get_connection())
        self._verifier = verifier or AuditChainVerifier(
            connection_factory=self._connection_factory
        )

    # â”€â”€ Listado sanitizado con filtros parametrizados â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def list_decisions(
        self,
        *,
        action: str | None = None,
        tool_name: str | None = None,
        risk_level: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lista decisiones desde la vista sanitizada con filtros opcionales.

        Todos los filtros se aplican con parÃ¡metros (nunca interpolaciÃ³n).
        Devuelve solo columnas autorizadas de la vista.
        """
        where: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if action:
            where.append("action = %(action)s")
            params["action"] = action
        if tool_name:
            where.append("tool_name = %(tool_name)s")
            params["tool_name"] = tool_name
        if risk_level:
            where.append("risk_level = %(risk_level)s")
            params["risk_level"] = risk_level
        if status:
            where.append("status = %(status)s")
            params["status"] = status

        sql = """
            SELECT
                decision_id,
                event_id,
                agent_id,
                agent_role,
                tool_name,
                operation_class,
                resource,
                environment,
                payload_digest,
                action,
                risk_level,
                reason_codes,
                policy_id,
                policy_version,
                effective_priority,
                conflicts,
                obligations,
                decided_at,
                expires_at,
                status,
                error_digest,
                result_digest,
                latency_post_ms,
                recorded_at
            FROM v_governance_console
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY decided_at DESC LIMIT %(limit)s OFFSET %(offset)s"

        conn = self._connection_factory()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    # â”€â”€ MÃ©tricas agregadas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def metrics(self) -> dict[str, Any]:
        """MÃ©tricas agregadas: conteos por acciÃ³n, percentiles y errores."""
        conn = self._connection_factory()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE action = 'PASS')  AS total_pass,
                        COUNT(*) FILTER (WHERE action = 'BLOCK') AS total_block,
                        COUNT(*) FILTER (WHERE action = 'HITL')  AS total_hitl,
                        COUNT(*)                                 AS total,
                        COUNT(*) FILTER (WHERE status = 'FAILED')  AS total_failed,
                        COUNT(*) FILTER (WHERE status = 'UNKNOWN') AS total_unknown,
                        COUNT(*) FILTER (WHERE status IS NULL)     AS total_no_result
                    FROM v_governance_console
                    """
                )
                row = cur.fetchone()
                counts = self._row_to_dict(row) if row else {}
                cur.execute(
                    """
                    SELECT
                        percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_post_ms)
                            AS p50,
                        percentile_cont(0.90) WITHIN GROUP (ORDER BY latency_post_ms)
                            AS p90,
                        percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_post_ms)
                            AS p99
                    FROM v_governance_console
                    WHERE latency_post_ms IS NOT NULL
                    """
                )
                pct_row = cur.fetchone()
                pct = self._row_to_dict(pct_row) if pct_row else {}
        finally:
            conn.close()
        return {
            "total": counts.get("total", 0) or 0,
            "pass": counts.get("total_pass", 0) or 0,
            "block": counts.get("total_block", 0) or 0,
            "hitl": counts.get("total_hitl", 0) or 0,
            "failed": counts.get("total_failed", 0) or 0,
            "unknown": counts.get("total_unknown", 0) or 0,
            "no_result": counts.get("total_no_result", 0) or 0,
            "latency_ms": {
                "p50": pct.get("p50"),
                "p90": pct.get("p90"),
                "p99": pct.get("p99"),
            },
        }

    # â”€â”€ Approvals PENDING / vencidas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def pending_approvals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Lista approvals PENDING (solo campos autorizados, sin argumentos)."""
        sql = """
            SELECT
                approval_id,
                event_id,
                decision_id,
                agent_id,
                tool_name,
                argument_digest,
                idempotency_key,
                state,
                argued_at,
                expires_at
            FROM approval_requests
            WHERE state = 'PENDING'
            ORDER BY argued_at ASC
            LIMIT %(limit)s
        """
        conn = self._connection_factory()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, {"limit": limit})
                rows = cur.fetchall()
                return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def expired_approvals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Lista approvals vencidas (expires_at en el pasado, aÃºn PENDING)."""
        sql = """
            SELECT
                approval_id,
                event_id,
                decision_id,
                agent_id,
                tool_name,
                argument_digest,
                idempotency_key,
                state,
                argued_at,
                expires_at
            FROM approval_requests
            WHERE state = 'PENDING' AND expires_at IS NOT NULL AND expires_at <= now()
            ORDER BY expires_at ASC
            LIMIT %(limit)s
        """
        conn = self._connection_factory()
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, {"limit": limit})
                rows = cur.fetchall()
                return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    # â”€â”€ Estado de la cadena de auditorÃ­a â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def audit_status(self) -> dict[str, Any]:
        """Estado de la cadena de auditorÃ­a (Ã­ntegra o motivo sanitizado)."""
        try:
            ok, reason, sequence = self._verifier.verify()
        except Exception as exc:  # noqa: BLE001 - infraestructura
            log.warning("audit_status_error", error=str(exc))
            return {"ok": False, "reason": "verification_error", "sequence": None}
        return {"ok": ok, "reason": reason, "sequence": sequence}

    # â”€â”€ Utilidades â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convierte un row psycopg (dict o tuple) a dict."""
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        # Row tipo fila psycopg: sin nombres de columna disponibles aquÃ­.
        # Los callers que necesitan nombres usan dict_row; este fallback
        # preserva el orden posicional.
        return {f"col_{i}": v for i, v in enumerate(row)}