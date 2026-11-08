"""Tests del runner de la demo del dashboard.

Verifican las funciones puras y el parser de logs de
`argentgob.dashboard.demo_runner` sin tocar subprocesos ni la base de datos:

- `demo_available()` refleja si `scripts/demo_end_to_end.py` existe.
- `_parse_escenarios()` extrae titulo/explicacion/outcome de la narrativa.
- La snapshot y el estado persisten entre llamadas (módulo-level).
"""
from __future__ import annotations

from pathlib import Path

from argentgob.dashboard import demo_runner


def _narrativa_valida() -> list[str]:
    return [
        "\n",
        "=" * 72 + "\n",
        "ESCENARIO: Consulta benigna (PASS)\n",
        "El agente pide el precio de AAPL. El PEP predice LOW, la politica\n",
        "YAML del perfil analyst lo permite y la tool se ejecuta.\n",
        "-" * 72 + "\n",
        "outcome: OK: precio AAPL\n",
        "\n",
        "=" * 72 + "\n",
        "ESCENARIO: Flujo HITL completo (aprobacion humana)\n",
        "Prediccion MEDIUM -> approval -> resolucion aprobada.\n",
        "outcome: HITL COMPLETO (approved -> resume -> ejecucion)\n",
        "\n",
        "=" * 72 + "\n",
        "RESUMEN DE LA DEMO\n",
        "=" * 72 + "\n",
        "ESCENARIO: Consulta benigna (PASS)\n",
        "outcome: OK: precio AAPL\n",
        "ESCENARIO: Flujo HITL completo (aprobacion humana)\n",
        "outcome: HITL COMPLETO (approved -> resume -> ejecucion)\n",
    ]


def test_demo_available_refleja_script():
    """demo_available() es True si existe scripts/demo_end_to_end.py."""
    repo = Path(__file__).resolve().parents[2]
    assert (repo / "scripts" / "demo_end_to_end.py").exists()
    assert demo_runner.demo_available() is True


def test_parse_escenarios_captura_titulo_y_outcome():
    escenarios = demo_runner._parse_escenarios(_narrativa_valida())
    assert len(escenarios) == 2
    assert escenarios[0]["titulo"] == "Consulta benigna (PASS)"
    assert escenarios[0]["outcome"] == "OK: precio AAPL"
    assert "el precio de AAPL" in escenarios[0]["explicacion"]
    assert "HITL COMPLETO" in escenarios[1]["outcome"]


def test_parse_escenarios_ignora_logs_estructurados():
    """El parser no se rompe con líneas structlog y devuelve solo escenarios."""
    log = [
        '2026-11-08T12:00:00Z [info ] demo_escenario titulo=Consulta benigna (PASS)\n',
        "ESCENARIO: Consulta benigna (PASS)\n",
        "outcome: OK: precio AAPL\n",
    ]
    escenarios = demo_runner._parse_escenarios(log)
    assert len(escenarios) == 1
    assert escenarios[0]["titulo"] == "Consulta benigna (PASS)"
    assert escenarios[0]["outcome"] == "OK: precio AAPL"


def test_snapshot_inicial_estado_vacio():
    snap = demo_runner.demo_status_snapshot()
    assert snap["running"] is False
    assert snap["lines"] == ""


def test_limpiar_log_vacia_buffer():
    demo_runner.limpiar_log()
    with demo_runner._state["lock"]:
        demo_runner._state["lines"] = ["linea-1\n", "linea-2\n"]
    demo_runner.limpiar_log()
    assert demo_runner.demo_status_snapshot()["lines"] == ""