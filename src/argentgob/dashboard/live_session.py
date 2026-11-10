"""Estado de sesión de un run financiero en vivo (AV-03).

Este módulo es lógica pura (sin Streamlit) para poder probarse con AppTest y
con tests unitarios:

- La intención del operador se resume en una clave de idempotencia determinista:
  un doble clic o un rerun del script reenvía la **misma** clave y la API
  devuelve el run existente en lugar de crear otro proceso (T-B02).
- El cursor de eventos avanza solo con lo recibido; el polling se detiene en
  estado terminal (T-B07).
- Un fallo de transporte no borra la sesión: se informa como API caída y se
  permite reintentar el sondeo sin reenviar el POST (T-B09).
- Los eventos se clasifican en categorías de presentación (herramienta,
  decisión, ejecución, contenido, HITL) sin exponer argumentos operativos.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from argentgob.live.events import TERMINAL_STATES

# Categorías de presentación del timeline (contrato §4, unidad 3 de AV-03).
CATEGORY_TOOL = "herramienta"
CATEGORY_DECISION = "decisión"
CATEGORY_EXECUTION = "ejecución"
CATEGORY_CONTENT = "contenido"
CATEGORY_HITL = "HITL"
CATEGORY_RUN = "run"

_KIND_CATEGORY: dict[str, str] = {
    "run.accepted": CATEGORY_RUN,
    "run.started": CATEGORY_RUN,
    "run.finished": CATEGORY_RUN,
    "tool.requested": CATEGORY_TOOL,
    "tool.started": CATEGORY_EXECUTION,
    "tool.completed": CATEGORY_EXECUTION,
    "tool.failed": CATEGORY_EXECUTION,
    "governance.decided": CATEGORY_DECISION,
    "content.released": CATEGORY_CONTENT,
    "content.withheld": CATEGORY_CONTENT,
    "approval.pending": CATEGORY_HITL,
    "approval.resolved": CATEGORY_HITL,
    "execution.resumed": CATEGORY_EXECUTION,
}

# Estados que la UI debe rotular como "no terminal, esperando algo".
WAITING_STATES: frozenset[str] = frozenset({"WAITING_APPROVAL", "RESUMING"})

MAX_EVENTS_KEPT = 500


def category_for(kind: str) -> str:
    """Categoría de presentación de un `kind` (desconocido → run)."""
    return _KIND_CATEGORY.get(kind, CATEGORY_RUN)


def build_idempotency_key(
    *, actor: str, scenario_id: str, profile: str, asset: str, request_text: str | None
) -> str:
    """Clave determinista de la intención (misma intención → mismo run).

    Se usa SHA-256 truncado con prefijo legible; el texto de la intención no se
    envía como clave para no filtrar contenido del operador en la API.
    """
    canonical = "\x1f".join(
        [
            actor.strip().lower(),
            scenario_id.strip().lower(),
            profile.strip().lower(),
            asset.strip().upper(),
            (request_text or "").strip(),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]
    return f"ui-{digest}"


@dataclass
class LiveSession:
    """Intención + run + cursor de una sesión de UI."""

    actor: str
    profile: str
    asset: str
    request_text: str | None = None
    scenario_id: str = "finanzas"
    idempotency_key: str = ""
    run_id: str | None = None
    state: str = ""
    cursor: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    api_unavailable: bool = False
    truncated: bool = False
    first_available_seq: int = 1
    polls: int = 0

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = build_idempotency_key(
                actor=self.actor,
                scenario_id=self.scenario_id,
                profile=self.profile,
                asset=self.asset,
                request_text=self.request_text,
            )

    # ── Derivados ────────────────────────────────────────────────────
    @property
    def terminal(self) -> bool:
        """True si el run alcanzó un estado terminal (polling debe cesar)."""
        return self.state in TERMINAL_STATES

    @property
    def waiting_approval(self) -> bool:
        """True si el run espera una aprobación humana (no aprobada)."""
        return self.state in WAITING_STATES

    @property
    def started(self) -> bool:
        return self.run_id is not None

    def timeline(self) -> list[dict[str, Any]]:
        """Eventos con categoría y etiqueta segura para la tabla de la UI."""
        rows: list[dict[str, Any]] = []
        for event in self.events:
            kind = str(event.get("kind", ""))
            rows.append(
                {
                    "seq": event.get("seq"),
                    "timestamp": event.get("timestamp"),
                    "categoria": category_for(kind),
                    "evento": kind,
                    "herramienta": event.get("tool") or "—",
                    "accion": event.get("action") or "—",
                    "estado": event.get("state") or event.get("execution_status") or "—",
                    "detalle": _safe_detail(event),
                }
            )
        return rows

    def pending_approvals(self) -> list[dict[str, Any]]:
        """Aprobaciones pendientes visibles en el timeline (nunca auto-aprobadas)."""
        pending: list[dict[str, Any]] = []
        resolved = {
            str(e.get("approval_ref"))
            for e in self.events
            if e.get("kind") == "approval.resolved" and e.get("approval_ref")
        }
        for event in self.events:
            if event.get("kind") != "approval.pending":
                continue
            ref = str(event.get("approval_ref") or "")
            if ref and ref in resolved:
                continue
            pending.append(
                {
                    "approval_ref": ref or "—",
                    "herramienta": event.get("tool") or "—",
                    "seq": event.get("seq"),
                }
            )
        return pending

    def partiality_note(self) -> str | None:
        """Explica por qué el run no es un éxito completo (si aplica)."""
        if self.state == "PARTIAL":
            return (
                "Resultado parcial: hubo acciones retenidas para aprobación o "
                "bloqueadas por gobernanza."
            )
        if self.state == "BLOCKED":
            return "Ninguna acción requerida pudo ejecutarse por gobernanza."
        if self.state == "INDETERMINATE":
            return "Efecto desconocido tras un timeout: no se reintenta automáticamente."
        if self.state in ("FAILED", "TIMED_OUT"):
            return "El run terminó sin resultado válido."
        return None


def _safe_detail(event: dict[str, Any]) -> str:
    """Detalle corto y seguro: solo campos de la allowlist ya saneados."""
    parts: list[str] = []
    codes = event.get("reason_codes")
    if isinstance(codes, list) and codes:
        parts.append(", ".join(str(c) for c in codes[:4]))
    message = event.get("message")
    if message:
        parts.append(str(message))
    risk = event.get("risk_level")
    if risk:
        parts.append(f"riesgo {risk}")
    content = event.get("content_status")
    if content:
        parts.append(f"contenido {content}")
    return " · ".join(parts) if parts else "—"


def apply_snapshot(session: LiveSession, snapshot: dict[str, Any]) -> LiveSession:
    """Incorpora un snapshot de `GET /runs/{id}` sin duplicar eventos."""
    session.state = str(snapshot.get("state") or session.state)
    session.truncated = bool(snapshot.get("truncated", False))
    session.first_available_seq = int(snapshot.get("first_available_seq") or 1)
    next_seq = int(snapshot.get("next_seq") or session.cursor)
    incoming = snapshot.get("events") or []
    if isinstance(incoming, list):
        known = {int(e.get("seq", 0)) for e in session.events}
        for event in incoming:
            if not isinstance(event, dict):
                continue
            seq = int(event.get("seq") or 0)
            if seq and seq not in known:
                session.events.append(event)
                known.add(seq)
        session.events.sort(key=lambda e: int(e.get("seq") or 0))
        if len(session.events) > MAX_EVENTS_KEPT:
            session.events = session.events[-MAX_EVENTS_KEPT:]
    session.cursor = max(session.cursor, next_seq)
    result = snapshot.get("result")
    if isinstance(result, dict):
        session.result = result
    session.error = None
    session.api_unavailable = False
    session.polls += 1
    return session


def poll(session: LiveSession, client: Any) -> LiveSession:
    """Un sondeo del run activo; no reenvía el POST ni crea procesos."""
    if session.run_id is None:
        return session
    if session.terminal:
        return session
    try:
        snapshot = client.get_run(
            session.run_id, actor=session.actor, after_seq=session.cursor
        )
    except Exception as exc:  # noqa: BLE001 - API caída es un estado de UI
        session.error = getattr(exc, "message", None) or "API no disponible."
        session.api_unavailable = bool(getattr(exc, "unavailable", True))
        return session
    return apply_snapshot(session, snapshot)


def start(session: LiveSession, client: Any) -> LiveSession:
    """Admite el run una sola vez; repeticiones devuelven el mismo `run_id`."""
    if session.run_id is not None:
        return session
    try:
        accepted = client.create_run(
            actor=session.actor,
            scenario_id=session.scenario_id,
            profile=session.profile,
            asset=session.asset,
            request_text=session.request_text,
            idempotency_key=session.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 - error seguro para la UI
        session.error = getattr(exc, "message", None) or "No se pudo admitir el run."
        session.api_unavailable = bool(getattr(exc, "unavailable", True))
        return session
    session.run_id = str(accepted.get("run_id") or "") or None
    session.state = str(accepted.get("state") or "STARTING")
    session.error = None
    session.api_unavailable = False
    return session
