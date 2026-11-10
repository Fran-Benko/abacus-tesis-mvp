"""Gate G-AV-03: UI financiera en vivo contra el stack desplegado.

Comprueba, con la API real y el LLM local real:

1. `/ready` del servicio de runs (esquema, catálogo, LLM, cupo).
2. Admisión por intención explícita y visibilidad incremental ≤ 3 s con el run
   todavía activo (T-B06).
3. Cursor monotónico sin duplicados y polling detenido en terminal (T-B05/T-B07).
4. Repetición de la misma intención sin segundo proceso (T-B02).
5. Actor ajeno sin visibilidad (T-B03).
6. Timeline sin argumentos operativos ni consola raw (T-P05).
7. La vista del dashboard renderiza con AppTest y no expone payload crudo.
8. La demo determinista anterior sigue disponible (T-R02).

Uso (dentro de la red del proyecto):
    python scripts/verify_live_ui_av03.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

VISIBILITY_BUDGET_SECONDS = 3.0
RUN_TIMEOUT_SECONDS = 240

_checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Registra un check del gate (sin datos sensibles)."""
    _checks.append((name, bool(ok), detail))
    estado = "OK" if ok else "FAIL"
    print(f"[{estado}] {name}" + (f" · {detail}" if detail else ""))


def _client():
    from argentgob.dashboard.live_client import LiveApiClient, _service_token

    return LiveApiClient(
        base_url=os.environ.get("AGENT_API_BASE", "http://agent:8090"),
        service_token=_service_token(),
        timeout=10,
    )


def _session(actor: str, asset: str):
    from argentgob.dashboard.live_session import LiveSession

    return LiveSession(actor=actor, profile="analyst", asset=asset)


def main() -> int:
    from argentgob.dashboard import live_session

    client = _client()

    # 1. Readiness del servicio.
    try:
        ready = client.ready()
        checks = ready.get("checks") or {}
        caps = ready.get("capabilities") or {}
        check("ready.checks", bool(ready.get("ready")), json.dumps(checks, sort_keys=True))
        check("ready.llm", bool(caps.get("llm_reachable")), "llm_reachable")
        check("ready.cupo", bool(caps.get("capacity_free")), "capacity_free")
    except Exception as exc:  # noqa: BLE001 - gate reporta el fallo
        check("ready", False, type(exc).__name__)
        return _summary()

    # 2. Admisión + visibilidad incremental.
    session = _session("operador-av03", "AAPL")
    live_session.start(session, client)
    check("admisión", session.run_id is not None, session.error or "run admitido")
    if session.run_id is None:
        return _summary()

    started = time.monotonic()
    first_event_at: float | None = None
    while time.monotonic() - started < RUN_TIMEOUT_SECONDS:
        live_session.poll(session, client)
        if session.events and first_event_at is None:
            first_event_at = time.monotonic() - started
            check(
                "visibilidad.incremental",
                first_event_at <= VISIBILITY_BUDGET_SECONDS and not session.terminal,
                f"{first_event_at:.2f}s · estado {session.state}",
            )
        if session.terminal:
            break
        time.sleep(0.25)

    check("run.terminal", session.terminal, session.state)
    check("run.resultado", session.result is not None, "informe proyectado")

    seqs = [int(e["seq"]) for e in session.events]
    check("eventos.orden", seqs == sorted(seqs) and len(seqs) == len(set(seqs)), f"{len(seqs)} eventos")
    check("cursor.final", session.cursor == (max(seqs) if seqs else 0), f"cursor {session.cursor}")

    polls_before = session.polls
    live_session.poll(session, client)
    check("polling.detenido", session.polls == polls_before, "sin sondeo tras terminal")

    # 3. Idempotencia de la intención.
    repeat = _session("operador-av03", "AAPL")
    check("idempotencia.clave", repeat.idempotency_key == session.idempotency_key, "misma clave")
    live_session.start(repeat, client)
    check("idempotencia.run", repeat.run_id == session.run_id, "mismo run")

    # 4. Actor ajeno.
    intruso = _session("operador-av03-ajeno", "AAPL")
    intruso.run_id = session.run_id
    live_session.poll(intruso, client)
    check(
        "actor.ajeno",
        intruso.error is not None and "no existe" in (intruso.error or ""),
        intruso.error or "sin error",
    )

    # 5. Timeline sin datos operativos.
    rows = session.timeline()
    serialized = json.dumps(rows, ensure_ascii=False, default=str)
    prohibidos = ("execution_arguments", "raw_payload", "Traceback", "sk-")
    check(
        "timeline.sin.raw",
        not any(token in serialized for token in prohibidos),
        f"{len(rows)} filas",
    )
    categorias = {row["categoria"] for row in rows}
    check("timeline.categorias", len(categorias) >= 2, ", ".join(sorted(categorias)))

    # 6. Vista del dashboard con AppTest (sin stack adicional).
    try:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(
            str(ROOT / "tests" / "unit" / "_live_finance_app.py"), default_timeout=60
        )
        app.run()
        texto = str(app)
        check("ui.render", not app.exception, "sin excepciones")
        check(
            "ui.sin.raw",
            "execution_arguments" not in texto and "raw_payload" not in texto,
            "sin payload crudo",
        )
    except Exception as exc:  # noqa: BLE001 - gate reporta el fallo
        check("ui.render", False, type(exc).__name__)

    # 7. Demo determinista anterior disponible (T-R02).
    check(
        "demo.anterior",
        (ROOT / "scripts" / "demo_end_to_end.py").is_file(),
        "scripts/demo_end_to_end.py",
    )

    return _summary()


def _summary() -> int:
    passed = sum(1 for _, ok, _ in _checks if ok)
    failed = len(_checks) - passed
    print(f"\n===== G-AV-03: {passed} passed, {failed} failed =====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
