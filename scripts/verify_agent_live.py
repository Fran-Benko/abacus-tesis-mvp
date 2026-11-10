"""Gate agregado ligero por hito (AV-01).

Reutiliza pytest y los runners existentes; no introduce un framework de
manifests. Valida prerrequisitos, ejecuta los selectores del hito, comprueba
que la colección no esté vacía y emite un resumen sanitizado.

Uso:
    python scripts/verify_agent_live.py --milestone AV-01
    python scripts/verify_agent_live.py --milestone AV-01 --with-llm
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Selectores por hito. Se amplían en cada AV; nunca se declaran vacíos.
MILESTONE_SELECTORS: dict[str, list[str]] = {
    "AV-01": [
        "tests/unit/test_run_events.py",
        "tests/unit/test_run_supervisor.py",
        "tests/unit/test_agent_server.py",
        "tests/unit/test_pep_observer.py",
        "tests/unit/test_run_aggregation.py",
        "tests/integration/test_agent_live_flow.py",
        "tests/unit/test_pep.py",
        "tests/unit/test_guardrails.py",
        "tests/unit/test_logger.py",
        "tests/integration/test_crewai_real.py",
    ],
}

# Pruebas que exigen llama.cpp real (opt-in explícito).
LLM_SELECTORS: dict[str, list[str]] = {
    "AV-01": ["tests/integration/test_agent_live_llm.py"],
}


def _run_pytest(selectors: list[str], *, extra_args: list[str] | None = None) -> dict:
    """Ejecuta pytest sobre los selectores y devuelve un resumen sanitizado."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        *selectors,
        "-q",
        "-p",
        "no:cacheprovider",
        *(extra_args or []),
    ]
    completed = subprocess.run(  # noqa: S603 - comando fijo, sin shell
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    tail = (completed.stdout or "").strip().splitlines()[-1:] or [""]
    return {
        "selectors": selectors,
        "exit_code": completed.returncode,
        "summary": tail[0][:200],
    }


def _collect_only(selectors: list[str]) -> int:
    """Cuenta los tests descubiertos; 0 significa selector vacío o roto."""
    completed = subprocess.run(  # noqa: S603 - comando fijo, sin shell
        [sys.executable, "-m", "pytest", *selectors, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return 0
    for line in reversed((completed.stdout or "").strip().splitlines()):
        if "test" in line and "collected" in line:
            digits = "".join(ch for ch in line.split("collected")[0] if ch.isdigit())
            if digits:
                return int(digits)
    return 0


def _preflight() -> dict[str, bool]:
    """Comprueba prerrequisitos sin imprimir valores sensibles."""
    return {
        "src_present": (ROOT / "src" / "argentgob" / "live").is_dir(),
        "fastapi_importable": _module_available("fastapi"),
        "llm_base_configured": bool(os.environ.get("OPENAI_API_BASE")),
        "database_configured": bool(os.environ.get("DATABASE_URL")),
    }


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except Exception:  # noqa: BLE001 - ausencia de dependencia es un dato
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate agregado de hitos AV")
    parser.add_argument("--milestone", required=True, choices=sorted(MILESTONE_SELECTORS))
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="incluye las pruebas que requieren llama.cpp real",
    )
    parser.add_argument(
        "--report",
        default="",
        help="ruta opcional para el resumen JSON sanitizado",
    )
    args = parser.parse_args(argv)

    selectors = MILESTONE_SELECTORS[args.milestone]
    preflight = _preflight()
    collected = _collect_only(selectors)
    report: dict = {
        "milestone_id": args.milestone,
        "gate_id": f"G-{args.milestone}",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "preflight": preflight,
        "collected": collected,
        "runs": [],
        "llm_runs": [],
        "result": "FAIL",
    }

    if collected == 0:
        report["limitations"] = ["selección de tests vacía o no colectable"]
        _emit(report, args.report)
        return 2

    unit_run = _run_pytest(selectors)
    report["runs"].append(unit_run)
    ok = unit_run["exit_code"] == 0

    if args.with_llm:
        llm_selectors = LLM_SELECTORS.get(args.milestone, [])
        if llm_selectors and _collect_only(llm_selectors) > 0:
            llm_run = _run_pytest(llm_selectors, extra_args=["-m", "llm_local"])
            report["llm_runs"].append(llm_run)
            ok = ok and llm_run["exit_code"] == 0
        else:
            report["limitations"] = ["pruebas LLM no disponibles en este árbol"]
            ok = False

    report["result"] = "PASS" if ok else "FAIL"
    _emit(report, args.report)
    return 0 if ok else 1


def _emit(report: dict, path: str) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if path:
        Path(path).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
