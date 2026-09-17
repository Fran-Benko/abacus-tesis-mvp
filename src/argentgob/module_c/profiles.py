"""
Perfiles de agente con diferentes permisos.
Cada perfil define: herramientas permitidas, guardrails activos y archivo
de system prompt.
"""
from dataclasses import dataclass


@dataclass
class AgentProfile:
    """Perfil de agente con sus permisos y guardrails asociados."""

    name: str
    allowed_tools: list[str]
    active_guardrails: list[str]
    system_prompt_file: str
    max_tool_calls: int


PROFILES: dict[str, AgentProfile] = {
    "analyst": AgentProfile(
        name="analyst",
            allowed_tools=["news", "stock_price", "crypto_price"],
        active_guardrails=["query_injection", "topic_relevance", "rate_limit"],
        system_prompt_file="prompts/analyst.md",
        max_tool_calls=5,
    ),
    "analyst_restricted": AgentProfile(
        name="analyst_restricted",
        allowed_tools=["stock_price", "crypto_price"],  # Sin búsqueda web
        active_guardrails=["rate_limit"],
        system_prompt_file="prompts/analyst_restricted.md",
        max_tool_calls=5,
    ),
    "admin": AgentProfile(
        name="admin",
            allowed_tools=["news", "stock_price", "crypto_price"],
        active_guardrails=[],  # Sin guardrails (solo para debug/testing)
        system_prompt_file="prompts/admin.md",
        max_tool_calls=20,
    ),
}
