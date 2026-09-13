"""PolicyDecision: resultado de la evaluación de política ABAC."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from argentgob.core.errors import GovernanceAction, ReasonCode


@dataclass
class PolicyDecision:
    """Veredicto del motor de políticas para una tool call."""

    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action: GovernanceAction = GovernanceAction.PASS
    reason_code: ReasonCode = ReasonCode.ALLOWED
    policy_id: str | None = None
    decided_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
