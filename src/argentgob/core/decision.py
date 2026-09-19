"""PolicyDecision: resultado de la evaluación ABAC (PASS | BLOCK | HITL).

H5 — Round-trip de decisión completa: el modelo preserva todos los campos
entre el motor, el JSON Schema, el JSON, la base de datos y la vista:
`decision_id`, `action`, `risk_level`, `reason_codes`, `policy_id`,
`policy_version`, `policy_digest`, `matched_policy_ids`, `effective_priority`,
`conflicts`, `obligations`, `transform_spec`, `decided_at` y `expires_at`.

`policy_id`/`policy_version`/`policy_digest` son todos nulos solo cuando no hay
política aplicable; las listas vacías siguen siendo arrays, no nulos ni strings.
Los timestamps son UTC.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from argentgob.core.errors import (
    GovernanceAction,
    Obligation,
    ReasonCode,
    RiskLevel,
)


class PolicyDecision:
    """Decisión de política ABAC (PASS | BLOCK | HITL)."""

    def __init__(
        self,
        *,
        decision_id: str | None = None,
        action: GovernanceAction = GovernanceAction.PASS,
        reason_code: ReasonCode | None = None,
        reason_codes: list[ReasonCode] | None = None,
        risk_level: RiskLevel = RiskLevel.LOW,
        policy_id: str | None = None,
        policy_version: int | None = None,
        policy_digest: str | None = None,
        matched_policy_ids: list[str] | None = None,
        effective_priority: int | None = None,
        conflicts: list[str] | None = None,
        obligations: list[Obligation] | None = None,
        transform_spec: dict[str, Any] | None = None,
        decided_at: datetime | None = None,
        expires_at: datetime | None = None,
    ):
        self.decision_id = decision_id or str(uuid.uuid4())
        self.action = action
        if reason_codes is not None:
            self.reason_codes = list(reason_codes)
        elif reason_code is not None:
            self.reason_codes = [reason_code]
        else:
            self.reason_codes = []
        self.risk_level = risk_level
        self.policy_id = policy_id
        self.policy_version = policy_version
        self.policy_digest = policy_digest
        self.matched_policy_ids = list(matched_policy_ids or [])
        self.effective_priority = effective_priority
        self.conflicts = list(conflicts or [])
        self.obligations = list(obligations or [])
        self.transform_spec = transform_spec
        self.decided_at = decided_at or datetime.now(timezone.utc)
        self.expires_at = expires_at

    # ── Compatibilidad con R2 (campo singular) ────────────────────────
    @property
    def reason_code(self) -> ReasonCode:
        """Reason code principal (primero de la lista, o ALLOWED por defecto)."""
        if self.reason_codes:
            return self.reason_codes[0]
        return ReasonCode.ALLOWED

    @reason_code.setter
    def reason_code(self, value: ReasonCode) -> None:
        """Asigna el reason code principal reemplazando la lista."""
        self.reason_codes = [value] if value is not None else []

    def is_expired(self, now: datetime | None = None) -> bool:
        """True si la decisión venció (expires_at en el pasado)."""
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serializa la decisión a un dict JSON-compatible (round-trip)."""
        return {
            "decision_id": self.decision_id,
            "action": self.action.value,
            "reason_codes": [r.value for r in self.reason_codes],
            "risk_level": self.risk_level.value,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "matched_policy_ids": list(self.matched_policy_ids),
            "effective_priority": self.effective_priority,
            "conflicts": list(self.conflicts),
            "obligations": [o.value for o in self.obligations],
            "transform_spec": self.transform_spec,
            "decided_at": self.decided_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyDecision":
        """Reconstruye una decisión desde un dict JSON-compatible."""
        return cls(
            decision_id=data["decision_id"],
            action=GovernanceAction(data["action"]),
            reason_codes=[ReasonCode(r) for r in data.get("reason_codes", [])],
            risk_level=RiskLevel(data.get("risk_level", RiskLevel.LOW.value)),
            policy_id=data.get("policy_id"),
            policy_version=data.get("policy_version"),
            policy_digest=data.get("policy_digest"),
            matched_policy_ids=list(data.get("matched_policy_ids", [])),
            effective_priority=data.get("effective_priority"),
            conflicts=list(data.get("conflicts", [])),
            obligations=[Obligation(o) for o in data.get("obligations", [])],
            transform_spec=data.get("transform_spec"),
            decided_at=_parse_utc(data["decided_at"]),
            expires_at=(
                _parse_utc(data["expires_at"]) if data.get("expires_at") else None
            ),
        )


def _parse_utc(value: str) -> datetime:
    """Parsea un timestamp ISO a datetime UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)