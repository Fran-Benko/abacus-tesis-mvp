"""
Demo end-to-end de ArgentGob-Mesh: gobernanza de agentes de principio a fin.

Ejecuta los 5 escenarios de `scripts/demo_lib.py` sobre los componentes REALES
(middleware, evaluador, guardrails, auditoría, HITL, orquestador de ejecución),
mostrando la narrativa de qué ocurre y por qué en cada paso, con logs
estructurados legibles (structlog).

Escenarios:
1. Consulta benigna        -> PASS  (predicción LOW, ejecuta, audita)
2. Predicción HIGH         -> BLOCK (INV-02: no ejecuta)
3. Guardrail inyección SQL -> BLOCK (razón de guardrail)
4. Sensibilidad política   -> BLOCK (SENSITIVITY_EXCEEDED)
5. HITL completo           -> approval -> resolución APROBADA -> resume (1-uso)
                              -> ejecución gobernada (1 vez)

Uso (desde la raíz del repo, con la imagen):
    docker run --rm -v "${PWD}:/app" -w /app -e DATABASE_URL=... \\
        -e ENVIRONMENT=LOCAL -e LOG_LEVEL=INFO \\
        --network bridge --entrypoint python argentgob-mvp-agent:latest \\
        scripts/demo_end_to_end.py [--real]

Sin `--real` corre 100% en memoria (reproducible en cualquier lado, sin red ni
LLM). Con `--real` persiste en PostgreSQL (requiere el stack y el modelo).
"""
from __future__ import annotations

import os
import sys

# Permite `python scripts/demo_end_to_end.py` desde cualquier cwd:
# agrega la raíz del repo al path para importar scripts.demo_lib.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from argentgob.observability.logger import setup_logging
from scripts.demo_lib import ejecutar_demo

setup_logging()


def main() -> None:
    real = "--real" in sys.argv
    if real:
        print("\n► MODO REAL: persistiendo en PostgreSQL (stack).\n")
    else:
        print("\n► MODO DEMO: en memoria, sin PostgreSQL ni red.\n")

    escenarios = ejecutar_demo(real=real)

    print("\n" + "=" * 72)
    print("RESUMEN DE LA DEMO")
    print("=" * 72)
    for esc in escenarios:
        print(esc.narrar())
    print("\nDemo completada. Los logs estructurados de cada evento de")
    print("gobernanza quedaron capturados arriba (policy_block, guardrail_pass,")
    print("predictive_inference, approval_*, hitl_*, tool_call_complete).\n")


if __name__ == "__main__":
    main()
