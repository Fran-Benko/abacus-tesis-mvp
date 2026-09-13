"""
Evaluador ABAC simplificado del MVP.

En el MVP la decisión se basa en si la herramienta está permitida para el
perfil. Intenta leer políticas desde PostgreSQL; si la base de datos no está
disponible, usa las políticas in-memory del perfil como fallback.

INV-11: una política vencida o inconsistente no autoriza. Ante duda -> BLOCK
(fail-closed).
"""
from argentgob.core.config import Settings
from argentgob.core.decision import PolicyDecision
from argentgob.core.envelope import ToolCallEnvelope
from argentgob.core.errors import GovernanceAction, ReasonCode
from argentgob.module_c.profiles import AgentProfile
from argentgob.observability.logger import get_logger

log = get_logger(__name__)


class ABACEvaluator:
    """Evalúa la política de acceso a herramientas por perfil."""

    def __init__(self, settings: Settings, profile: AgentProfile):
        self.settings = settings
        self.profile = profile

    def evaluate(self, envelope: ToolCallEnvelope) -> PolicyDecision:
        """Retorna PASS si la herramienta está permitida; BLOCK en caso contrario."""
        allowed_tools = self._load_allowed_tools()

        # El perfil admin puede declarar el comodín "*" (acceso total).
        if "*" in allowed_tools or envelope.tool_name in allowed_tools:
            log.info(
                "abac_allow",
                tool=envelope.tool_name,
                profile=self.profile.name,
            )
            return PolicyDecision(
                action=GovernanceAction.PASS,
                reason_code=ReasonCode.ALLOWED,
                policy_id=f"POL-{self.profile.name}-{envelope.tool_name}",
            )

        log.warning(
            "abac_block",
            tool=envelope.tool_name,
            profile=self.profile.name,
            allowed=allowed_tools,
            reason=ReasonCode.TOOL_NOT_ALLOWED.value,
        )
        return PolicyDecision(
            action=GovernanceAction.BLOCK,
            reason_code=ReasonCode.TOOL_NOT_ALLOWED,
            policy_id=None,
        )

    def _load_allowed_tools(self) -> list[str]:
        """Carga las herramientas permitidas desde PostgreSQL con fallback in-memory.

        En el MVP, si la base de datos no está disponible se degrada de forma
        segura a las políticas declaradas en el perfil (in-memory).
        """
        try:
            return self._load_from_db()
        except Exception as exc:  # noqa: BLE001 - fallback intencional de MVP
            log.warning(
                "abac_db_fallback",
                detail=str(exc),
                profile=self.profile.name,
            )
            return list(self.profile.allowed_tools)

    def _load_from_db(self) -> list[str]:
        """Lee las políticas ALLOW vigentes del perfil desde PostgreSQL."""
        # Import diferido para no requerir psycopg en tests unitarios sin DB.
        from argentgob.db.connection import get_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tool_name
                    FROM governance_policies
                    WHERE profile_name = %(profile)s
                      AND effect = 'ALLOW'
                      AND valid_from <= now()
                      AND (valid_until IS NULL OR valid_until > now())
                    """,
                    {"profile": self.profile.name},
                )
                rows = cur.fetchall()
            tools = [r[0] for r in rows]
            if not tools:
                # Sin filas: la DB está viva pero no hay políticas cargadas.
                # Degradar a la definición del perfil para no bloquear el MVP.
                return list(self.profile.allowed_tools)
            return tools
        finally:
            conn.close()
