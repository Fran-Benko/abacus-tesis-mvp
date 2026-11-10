"""Proceso hijo de un run financiero (AV-01).

Un proceso por run, estado aislado. El hijo:

1. Construye el Crew financiero existente con `run_id` y un observador de cola.
2. Ejecuta `crew.kickoff()` (inferencia real contra llama.cpp).
3. Publica eventos por tentativa en la cola IPC y un mensaje final con el
   estado y el resultado ya proyectado.

Nunca publica stdout, prompts ni razonamiento: solo eventos de allowlist.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from argentgob.core.errors import HookAborted
from argentgob.live.events import QueueObserver, strip_unsafe_text
from argentgob.observability.logger import get_logger

log = get_logger(__name__)

MAX_RESULT_BYTES = 32768


@dataclass(frozen=True)
class ChildContext:
    """Datos mínimos que el hijo necesita para ejecutar el run."""

    run_id: str
    profile: str
    asset: str
    request: str | None
    max_event_bytes: int = 2048


def project_result(raw: Any) -> dict[str, Any]:
    """Proyecta el resultado del Crew a un objeto acotado y sin raw.

    El informe del LLM es texto no confiable: se limpia de ANSI/controles y se
    acota. Si excede el límite, se marca `truncated` en vez de publicar raw.
    """
    text = strip_unsafe_text(str(raw), max_chars=MAX_RESULT_BYTES)
    truncated = len(str(raw)) > MAX_RESULT_BYTES
    payload = {
        "summary": text,
        "limitations": [
            "Informe generado por LLM local; no es asesoramiento financiero.",
            "Proveedores de datos externos sujetos a disponibilidad.",
        ],
        "domain_status": "ANSWERED",
    }
    if truncated:
        payload["truncated"] = True
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        payload["summary"] = payload["summary"][: MAX_RESULT_BYTES // 2]
        payload["truncated"] = True
    return payload


def run_financial_child(ctx: ChildContext, queue: Any) -> None:
    """Ejecuta el run financiero en el proceso hijo y reporta por la cola."""
    observer = QueueObserver(queue, ctx.run_id, max_event_bytes=ctx.max_event_bytes)
    tally = _DecisionTally()

    def send(message: dict[str, Any]) -> None:
        try:
            queue.put(message, timeout=2.0)
        except Exception:  # noqa: BLE001 - la cola caída no debe colgar el hijo
            log.warning("child_queue_unavailable", run_id=ctx.run_id)

    try:
        from argentgob.agent.crew import build_analysis_crew

        crew = build_analysis_crew(
            asset_query=ctx.asset,
            profile_name=ctx.profile,
            run_id=ctx.run_id,
            observer=_TallyingObserver(observer, tally),
            verbose=False,
        )
        raw = crew.kickoff()
        send({"type": "result", "result": project_result(raw)})
        send({"type": "final", "state": tally.final_state(has_result=True)})
    except HookAborted as exc:
        # Gobernanza bloqueó la tentativa: no es un fallo técnico.
        send(
            {
                "type": "final",
                "state": tally.final_state(has_result=False, aborted=True),
                "message": strip_unsafe_text(str(exc.reason), max_chars=200),
            }
        )
    except Exception as exc:  # noqa: BLE001 - se reporta como fallo seguro
        log.error(
            "child_run_failed",
            run_id=ctx.run_id,
            error_type=type(exc).__name__,
        )
        send(
            {
                "type": "final",
                "state": "FAILED",
                "message": type(exc).__name__,
            }
        )


class _DecisionTally:
    """Cuenta decisiones por acción para agregar el estado del run (§4)."""

    def __init__(self) -> None:
        self.pass_count = 0
        self.hitl_count = 0
        self.block_count = 0

    def record(self, action: str | None) -> None:
        if action == "PASS":
            self.pass_count += 1
        elif action == "HITL":
            self.hitl_count += 1
        elif action == "BLOCK":
            self.block_count += 1

    def final_state(self, *, has_result: bool, aborted: bool = False) -> str:
        """Aplica la regla de agregación del contrato §4.

        - Sin ninguna ejecución autorizada y con bloqueos/retenciones → BLOCKED.
        - Resultado con acciones necesarias retenidas o bloqueadas → PARTIAL.
        - Resultado con todas las tentativas autorizadas → SUCCEEDED.
        """
        if self.pass_count == 0 and (self.hitl_count or self.block_count):
            return "BLOCKED"
        if aborted and not has_result:
            return "BLOCKED"
        if self.hitl_count or self.block_count:
            return "PARTIAL" if has_result else "BLOCKED"
        return "SUCCEEDED" if has_result else "PARTIAL"


class _TallyingObserver:
    """Envuelve el observador de cola y acumula decisiones para la agregación."""

    def __init__(self, inner: QueueObserver, tally: _DecisionTally) -> None:
        self._inner = inner
        self._tally = tally

    def on_governance_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "governance.decided":
            self._tally.record(payload.get("action"))
        self._inner.on_governance_event(kind, payload)
