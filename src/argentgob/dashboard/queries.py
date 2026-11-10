"""Acceso a datos del dashboard ArgentGob-Mesh.

Separa las consultas SQL de la presentación. Corrige:
- A2: `fetch_summary()` cuenta HITL y Otras (cierra el 100% del universo).
- B1: agregaciones sobre el universo completo (sin `limit=200`).
- B4: `GROUP BY reason_code` para motivos de bloqueo.
- A5: saneo de nulos (tool_name → «Sin herramienta», etc.).
- A8: búsqueda robusta a ambos formatos de ID (UUID y apr-/evt-/dec-).

INV-05: nunca se selecciona execution_arguments ni payload crudo.
"""
from __future__ import annotations

import os

import pandas as pd
import psycopg
from psycopg.rows import dict_row


def _normalize_db_url(url: str) -> str:
    """Convierte una URL estilo SQLAlchemy a una URL nativa de psycopg3."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


def _resolve_url() -> str:
    """DSN del dashboard: secreto de solo lectura o `DATABASE_URL` (AV-02)."""
    from argentgob.db.dsn import resolve_database_url

    try:
        return _normalize_db_url(resolve_database_url(role="dashboard"))
    except RuntimeError:
        return _normalize_db_url(os.environ.get("DATABASE_URL", ""))


DATABASE_URL = _resolve_url()


def get_connection():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def fetch_summary() -> dict:
    """Estadísticas agregadas de las decisiones (corrige A2: incluye HITL)."""
    sql = """
        SELECT
            COUNT(*)                                  AS total,
            COUNT(*) FILTER (WHERE action = 'PASS')  AS total_pass,
            COUNT(*) FILTER (WHERE action = 'BLOCK') AS total_block,
            COUNT(*) FILTER (WHERE action = 'HITL')  AS total_hitl,
            COUNT(*) FILTER (
                WHERE action NOT IN ('PASS', 'BLOCK', 'HITL') OR action IS NULL
            )                                        AS total_other,
            ROUND(AVG(latency_pre_ms)::numeric, 2)    AS avg_pre_ms
        FROM governance_decisions
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return cur.fetchone() or {}


def fetch_decisions(limit: int = 200) -> pd.DataFrame:
    """Lee las últimas decisiones (sin raw args)."""
    sql = """
        SELECT
            decision_id,
            agent_id,
            agent_role,
            tool_name,
            action,
            reason_code,
            payload_digest,
            latency_pre_ms,
            decided_at
        FROM governance_decisions
        ORDER BY decided_at DESC
        LIMIT %(limit)s
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"limit": limit})
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def fetch_events(limit: int = 100) -> pd.DataFrame:
    """Lee los últimos eventos de ejecución."""
    sql = """
        SELECT
            id,
            event_id,
            decision_id,
            tool_name,
            status,
            error_message,
            recorded_at
        FROM governance_events
        ORDER BY recorded_at DESC
        LIMIT %(limit)s
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"limit": limit})
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def decisiones_por_herramienta() -> pd.DataFrame:
    """Agregación sobre el universo completo (corrige B1)."""
    sql = """
        SELECT
            COALESCE(NULLIF(TRIM(tool_name), ''), 'Sin herramienta') AS tool_name,
            action,
            COUNT(*) AS count
        FROM governance_decisions
        GROUP BY 1, 2
        ORDER BY 1
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return (
        df.pivot(index="tool_name", columns="action", values="count")
        .fillna(0)
        .reindex(columns=["PASS", "HITL", "BLOCK"], fill_value=0)
    )


def motivos_bloqueo() -> pd.DataFrame:
    """Motivos de bloqueo agregados (corrige B4)."""
    sql = """
        SELECT reason_code, COUNT(*) AS count
        FROM governance_decisions
        WHERE action = 'BLOCK'
        GROUP BY reason_code
        ORDER BY count DESC
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def actividad_por_jornada() -> pd.DataFrame:
    """Conteo por jornada activa (YYYY-MM-DD) y acción (corrige B5)."""
    sql = """
        SELECT
            to_char(decided_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
            action,
            COUNT(*) AS count
        FROM governance_decisions
        GROUP BY 1, 2
        ORDER BY 1
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return (
        df.pivot(index="day", columns="action", values="count")
        .fillna(0)
        .reindex(columns=["PASS", "HITL", "BLOCK"], fill_value=0)
    )


def _saneo_nulos(df: pd.DataFrame) -> pd.DataFrame:
    """Reemplaza None por etiquetas explícitas (A5)."""
    if "tool_name" in df.columns:
        df["tool_name"] = df["tool_name"].fillna("Sin herramienta")
    if "agent_role" in df.columns:
        df["agent_role"] = df["agent_role"].fillna("No especificado")
    if "final_action" in df.columns:
        df["final_action"] = df["final_action"].fillna("Sin decisión vinculada")
    return df


def predicciones_saneadas(rows: list[dict]) -> pd.DataFrame:
    """Convierte filas de predicción a DataFrame con nulos saneados (A5)."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return _saneo_nulos(df)


def buscar_por_id(term: str) -> pd.DataFrame:
    """Búsqueda robusta a ambos formatos de ID (A8)."""
    sql = """
        SELECT decision_id, agent_id, tool_name, action, reason_code, decided_at
        FROM governance_decisions
        WHERE decision_id ILIKE %(term)s
           OR agent_id ILIKE %(term)s
        ORDER BY decided_at DESC
        LIMIT 50
    """
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"term": f"%{term}%"})
        rows = cur.fetchall()
    return pd.DataFrame(rows)