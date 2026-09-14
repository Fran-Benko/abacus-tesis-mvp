"""
Punto de entrada CLI del agente.
Uso: python -m argentgob.agent.main [ticker_o_nombre] [--profile analyst]
"""
import argparse
import sys
import uuid

from rich.console import Console
from rich.panel import Panel

from argentgob.agent.crew import build_analysis_crew
from argentgob.core.errors import HookAborted
from argentgob.observability.logger import setup_logging
from argentgob.observability.logger import get_logger

console = Console()
log = get_logger(__name__)


def main() -> None:
    """Ejecuta el análisis financiero gobernado desde la línea de comandos."""
    setup_logging()
    run_id = str(uuid.uuid4())
    log.info("agent_run_start", run_id=run_id)

    parser = argparse.ArgumentParser(
        description="ArgentGob-Mesh · Agente de Análisis Financiero"
    )
    parser.add_argument(
        "asset",
        nargs="?",
        help="Ticker o nombre del activo (Ej: AAPL, bitcoin)",
    )
    parser.add_argument(
        "--profile",
        default="analyst",
        choices=["analyst", "analyst_restricted", "admin"],
    )
    args = parser.parse_args()

    if not args.asset:
        args.asset = console.input(
            "🔎 Ingresá el ticker o nombre del activo a analizar: "
        )

    console.print(
        Panel(
            f"[bold green]ArgentGob-Mesh MVP[/bold green]\n"
            f"Activo: [cyan]{args.asset}[/cyan] | Perfil: [yellow]{args.profile}[/yellow]",
            title="🛡️ Agente Financiero Gobernado",
        )
    )

    try:
        log.info(
            "agent_build_crew",
            run_id=run_id,
            asset=args.asset,
            profile=args.profile,
        )
        crew = build_analysis_crew(
            asset_query=args.asset, profile_name=args.profile
        )
        result = crew.kickoff()
        log.info("agent_run_complete", run_id=run_id, status="SUCCESS")
        console.print(Panel(str(result), title="📊 Análisis Fundacional"))
    except HookAborted as e:
        log.warning("agent_run_blocked", run_id=run_id, error=str(e))
        console.print(
            f"[bold red]⛔ Acción bloqueada por gobernanza:[/bold red] {e}"
        )
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("agent_run_cancelled", run_id=run_id)
        console.print("[yellow]Análisis cancelado por el usuario.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
