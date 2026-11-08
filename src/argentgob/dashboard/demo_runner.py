"""Ejecución de la demo end-to-end desde el dashboard.

Responsabilidades:
- Lanzar `scripts/demo_end_to_end.py --real` como subproceso con el entorno
  del dashboard (persiste en PostgreSQL).
- Capturar el log estructurado del demo en streaming (anexo incremental).
- Producir el "resumen de escenarios" sin exponer payload crudo (INV-05):
  solo titulo, explicacion y outcome.

La demo NO usa el LLM (predictor stub interno) por lo que solo requiere que
PostgreSQL esté alcanzable y que `policies/` + `scripts/` existan en /app.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parents[3]  # raíz del repo (…/src/argentgob/dashboard -> /app)
_DEMO_SCRIPT = _APP_DIR / "scripts" / "demo_end_to_end.py"

# Estado del proceso único por dashboard (un solo run a la vez).
_state: dict[str, Any] = {
    "proc": None,
    "lines": [],
    "done": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "escenarios": [],
    "lock": threading.Lock(),
}


def demo_available() -> bool:
    """True si el entorno puede correr la demo (script presente)."""
    return _DEMO_SCRIPT.exists() and os.access(_DEMO_SCRIPT, os.R_OK)


def _read_impl(limit: int = 500) -> str:
    with _state["lock"]:
        lines = _state["lines"][-limit:]
    return "".join(lines)


def demo_status_snapshot() -> dict[str, Any]:
    """Snapshot del estado actual (para el rerun del dashboard)."""
    with _state["lock"]:
        proc = _state["proc"]
        return {
            "running": proc is not None and proc.poll() is None,
            "started_at": _state["started_at"],
            "finished_at": _state["finished_at"],
            "returncode": _state["returncode"],
            "done": _state["done"],
            "escenarios": list(_state["escenarios"]),
            "lines": "".join(_state["lines"][-500:]),
        }


def _parse_escenarios(lines: list[str]) -> list[dict[str, str]]:
    """Extrae titulo / explicacion / outcome de las cadenas ESCENARIO:.

    Sintaxis generada por demo_lib.EscenarioDemo.narrar():
        ESCENARIO: <titulo>
        <explicacion>
        outcome: <outcome>

    La narrativa completa (narrar) y el bloque RESUMEN DE LA DEMO repiten los
    mismos escenarios: se deduplican por (titulo, outcome) preservando orden.
    Es un parse de logs (best-effort): si algo no matchea, se omite.
    """
    result: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_expl = False
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("ESCENARIO: "):
            current = {
                "titulo": line[len("ESCENARIO: "):],
                "explicacion": "",
                "outcome": "",
            }
            in_expl = True
            result.append(current)
        elif current is not None and line.startswith("outcome: "):
            current["outcome"] = line[len("outcome: "):]
            in_expl = False
        elif current is not None and in_expl and line.strip():
            current["explicacion"] = (
                current["explicacion"] + " " + line.strip()
            ).strip()

    # Dedupe por (titulo, outcome) preservando el primer orden.
    seen = set()
    uniq: list[dict[str, str]] = []
    for e in result:
        key = (e["titulo"], e["outcome"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return uniq


def _reader(fd, container: list[str]) -> None:
    """Lee una pipe hasta EOF y acumula en `container`."""
    assert fd is not None
    while True:
        try:
            chunk = fd.readline()
        except Exception:  # noqa: BLE001 - pipe cerrada
            break
        if not chunk:
            break
        container.append(chunk)


def _worker(proc: subprocess.Popen) -> None:
    """Lee stdout+stderr en streaming (hilo por pipe) y acumula el log."""
    out_buf: list[str] = []
    err_buf: list[str] = []
    t_out = threading.Thread(target=_reader, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join(timeout=10)
    t_err.join(timeout=10)

    merged = out_buf + err_buf
    with _state["lock"]:
        _state["lines"].extend(merged)
        _state["done"] = True
        _state["finished_at"] = time.time()
        _state["returncode"] = proc.returncode
        _state["escenarios"] = _parse_escenarios(_state["lines"])


def limpiar_log() -> None:
    """Vacía el log acumulado (no afecta el proceso en curso)."""
    with _state["lock"]:
        _state["lines"] = []
        _state["escenarios"] = []


def start_demo() -> tuple[bool, str]:
    """Lanza la demo `--real` (persistente). No-op si ya hay una corriendo.

    Retorna (ok, mensaje). El resultado asincrónico se acumula en `_state`.
    """
    if not demo_available():
        return False, "scripts/demo_end_to_end.py no está disponible en esta imagen."
    with _state["lock"]:
        proc = _state["proc"]
        if proc is not None and proc.poll() is None:
            return False, "Ya hay una demo en ejecución."

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("ENVIRONMENT", "LOCAL")
    env.setdefault("LOG_LEVEL", "INFO")

    proc = subprocess.Popen(
        [sys.executable, str(_DEMO_SCRIPT), "--real"],
        cwd=str(_APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    with _state["lock"]:
        _state["proc"] = proc
        _state["lines"] = []
        _state["done"] = False
        _state["started_at"] = time.time()
        _state["finished_at"] = None
        _state["returncode"] = None
        _state["escenarios"] = []

    threading.Thread(target=_worker, args=(proc,), daemon=True).start()
    return True, "Demo iniciada."