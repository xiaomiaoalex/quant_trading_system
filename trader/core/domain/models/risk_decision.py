from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class RiskSizingDecisionType(str, Enum):
    APPROVE = "approve"
    CLIP = "clip"
    REJECT = "reject"
    CLOSE_ONLY = "close_only"


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    constraint_type: str
    max_qty: Decimal
    current_value: Decimal
    limit_value: Decimal
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "constraint_type": self.constraint_type,
            "max_qty": str(self.max_qty),
            "current_value": str(self.current_value),
            "limit_value": str(self.limit_value),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class RiskSizingDecision:
    requested_qty: Decimal
    normalized_qty: Decimal
    max_allowed_qty: Decimal
    final_qty: Decimal
    decision: RiskSizingDecisionType
    reason: str
    limiting_factor: str | None
    constraints: tuple[ConstraintResult, ...] = field(default_factory=tuple)
    trace_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_qty": str(self.requested_qty),
            "normalized_qty": str(self.normalized_qty),
            "max_allowed_qty": str(self.max_allowed_qty),
            "final_qty": str(self.final_qty),
            "decision": self.decision.value,
            "reason": self.reason,
            "limiting_factor": self.limiting_factor,
            "constraints": [c.to_dict() for c in self.constraints],
            "trace_id": self.trace_id,
        }

    @property
    def is_approval(self) -> bool:
        return self.decision == RiskSizingDecisionType.APPROVE

    @property
    def is_clip(self) -> bool:
        return self.decision == RiskSizingDecisionType.CLIP

    @property
    def is_rejection(self) -> bool:
        return self.decision == RiskSizingDecisionType.REJECT

    @property
    def is_close_only(self) -> bool:
        return self.decision == RiskSizingDecisionType.CLOSE_ONLY
