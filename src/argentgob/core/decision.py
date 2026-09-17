"""PolicyDecision: resultado de la evaluación de política ABAC."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from argentgob.core.errors import GovernanceAction, Obligation, ReasonCode


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
    # R2 — Frontera gobernada.
    expires_at: datetime | None = None
    obligations: list[Obligation] = field(default_factory=list)

    def is_expired(self, now: datetime | None = None) -> bool:
        """True si la decisión venció (expires_at en el pasado).

        Sin expires_at, la decisión nunca vence (comportamiento previo a R2).
        """
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now > self.expires_at
