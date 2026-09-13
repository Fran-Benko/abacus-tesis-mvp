"""
Definición del Crew de CrewAI con gobernanza integrada.
Cada herramienta es un GovernedTool configurado con el middleware.
"""
from pathlib import Path

from crewai import LLM, Agent, Crew, Task

from argentgob.core.config import get_settings
from argentgob.core.envelope import AgentIdentity
from argentgob.db.audit import AuditWriter
from argentgob.module_a.governance import GovernanceMiddleware
from argentgob.module_c.abac_evaluator import ABACEvaluator
from argentgob.module_c.guardrails import GuardrailEngine
from argentgob.module_c.profiles import PROFILES
from argentgob.tools.crypto_tool import CryptoPriceTool
from argentgob.tools.news_tool import DuckDuckGoNewsTool
from argentgob.tools.stock_tool import StockPriceTool


def build_analysis_crew(asset_query: str, profile_name: str = "analyst") -> Crew:
    """Construye un Crew de análisis financiero gobernado según el perfil."""
    settings = get_settings()
    profile = PROFILES[profile_name]

    # Cargar el system prompt del perfil.
    prompt_path = Path(profile.system_prompt_file)
    system_prompt = (
        prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    )

    # Identidad del agente.
    agent_id = AgentIdentity(id=f"agent-{profile_name}", role=profile_name)

    # Configurar el middleware de gobernanza.
    abac = ABACEvaluator(settings=settings, profile=profile)
    guardrails = GuardrailEngine(
        profile_guardrails=profile.active_guardrails,
        rate_limit=settings.rate_limit_calls_per_run,
    )
    audit = AuditWriter(settings=settings)
    middleware = GovernanceMiddleware(
        settings=settings, abac=abac, guardrails=guardrails, audit=audit
    )

    # Instanciar herramientas gobernadas según el perfil.
    all_tools = {
        "duckduckgo_news": DuckDuckGoNewsTool(
            governance=middleware, agent_identity=agent_id
        ),
        "stock_price": StockPriceTool(
            governance=middleware, agent_identity=agent_id
        ),
        "crypto_price": CryptoPriceTool(
            governance=middleware, agent_identity=agent_id
        ),
    }
    tools = [t for name, t in all_tools.items() if name in profile.allowed_tools]

    # Configurar el LLM local (llama-server expone API compatible con OpenAI).
    llm = LLM(
        model=f"openai/{settings.openai_model_name}",
        base_url=settings.openai_api_base,
        api_key=settings.openai_api_key,
        temperature=0.1,
        max_tokens=2048,
    )

    # Definir agente y tarea.
    analyst_agent = Agent(
        role="Analista Financiero",
        goal=f"Analizar el activo financiero: {asset_query}",
        backstory=system_prompt,
        tools=tools,
        llm=llm,
        verbose=True,
        max_iter=profile.max_tool_calls,
    )

    analysis_task = Task(
        description=(
            f"Realiza un análisis fundacional completo de '{asset_query}'. "
            "Busca las 3 últimas noticias relevantes, obtén el precio actual "
            "y elabora un análisis breve con consideraciones de riesgo."
        ),
        expected_output=(
            "Informe de análisis fundacional con: datos de mercado actuales, "
            "resumen de las 3 últimas noticias y análisis en 2-3 párrafos."
        ),
        agent=analyst_agent,
    )

    return Crew(
        agents=[analyst_agent],
        tasks=[analysis_task],
        verbose=True,
    )
