"""
Minimal Capital Allocator for Multi-Strategy Position Allocation
================================================================
Services Plane module for capital allocation across strategies.

Responsibilities:
- Net exposure & total exposure budget enforcement
- Same-direction signal budget competition
- Opposing signal netting or mutual exclusion
- Output approved/clipped/rejected with reason
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    pass


class AllocationDecision(Enum):
    APPROVED = "approved"
    CLIPPED = "clipped"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StrategyAllocationRequest:
    strategy_id: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    requested_size: float
    signal_confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class AllocationResult:
    decision: AllocationDecision
    approved_size: float
    rejected_size: float
    reason: str
    limiting_factor: str | None


@dataclass(frozen=True, slots=True)
class CapitalAllocatorConfig:
    total_exposure_budget: float
    net_exposure_limit: float
    same_direction_budget: float
    min_trade_size: float = 10.0
    max_request_size: float | None = None  # Optional cap on single request
    confidence_threshold: float = 0.5
    allow_opposing_offset: bool = True
    fail_closed: bool = True

    def __post_init__(self) -> None:
        # Validate thresholds are non-negative
        if self.total_exposure_budget < 0:
            raise ValueError(f"total_exposure_budget must be >= 0, got {self.total_exposure_budget}")
        if self.net_exposure_limit < 0:
            raise ValueError(f"net_exposure_limit must be >= 0, got {self.net_exposure_limit}")
        if self.same_direction_budget < 0:
            raise ValueError(f"same_direction_budget must be >= 0, got {self.same_direction_budget}")
        if self.min_trade_size < 0:
            raise ValueError(f"min_trade_size must be >= 0, got {self.min_trade_size}")
        if self.max_request_size is not None and self.max_request_size < 0:
            raise ValueError(f"max_request_size must be >= 0, got {self.max_request_size}")
        if not (0.0 <= self.confidence_threshold <= 1.0):
            raise ValueError(f"confidence_threshold must be in [0, 1], got {self.confidence_threshold}")


class PortfolioStateProviderPort(Protocol):
    """Port for providing current portfolio state to the allocator."""

    def get_net_exposure(self, symbol: str | None = None) -> float:
        ...

    def get_total_exposure(self) -> float:
        ...

    def get_exposure_by_side(self, side: Literal["LONG", "SHORT"]) -> float:
        ...

    def get_position_size(self, symbol: str, side: Literal["LONG", "SHORT"]) -> float:
        ...


class SimplePortfolioState:
    """Simple in-memory portfolio state provider for testing."""

    def __init__(
        self,
        net_exposure: float = 0.0,
        total_exposure: float = 0.0,
        long_exposure: float = 0.0,
        short_exposure: float = 0.0,
        positions: dict[str, dict[Literal["LONG", "SHORT"], float]] | None = None,
    ):
        self._net_exposure = net_exposure
        self._total_exposure = total_exposure
        self._long_exposure = long_exposure
        self._short_exposure = short_exposure
        self._positions = positions or {}

    def get_net_exposure(self, symbol: str | None = None) -> float:
        if symbol is not None:
            pos = self._positions.get(symbol, {})
            long_sz = pos.get("LONG", 0.0)
            short_sz = pos.get("SHORT", 0.0)
            return long_sz - short_sz
        return self._net_exposure

    def get_total_exposure(self) -> float:
        return self._total_exposure

    def get_exposure_by_side(self, side: Literal["LONG", "SHORT"]) -> float:
        if side == "LONG":
            return self._long_exposure
        return self._short_exposure

    def get_position_size(self, symbol: str, side: Literal["LONG", "SHORT"]) -> float:
        return self._positions.get(symbol, {}).get(side, 0.0)


class CapitalAllocator:
    """Minimal multi-strategy capital allocator."""

    def __init__(self, config: CapitalAllocatorConfig):
        self._config = config

    def allocate(
        self, request: StrategyAllocationRequest, current_state: PortfolioStateProviderPort
    ) -> AllocationResult:
        if self._config.fail_closed:
            if not request.strategy_id:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason="Missing or empty strategy_id",
                    limiting_factor="strategy_id",
                )
            if not request.symbol:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason="Missing or empty symbol",
                    limiting_factor="symbol",
                )

        if math.isnan(request.requested_size) or math.isinf(request.requested_size):
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason="Invalid requested_size (NaN or Inf)",
                limiting_factor="requested_size",
            )
        if math.isnan(request.signal_confidence) or math.isinf(request.signal_confidence):
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason="Invalid signal_confidence (NaN or Inf)",
                limiting_factor="signal_confidence",
            )

        if request.requested_size <= 0.0:
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason="Zero or negative requested_size",
                limiting_factor="requested_size",
            )

        if request.signal_confidence < self._config.confidence_threshold:
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason=f"Signal confidence {request.signal_confidence:.2f} below threshold {self._config.confidence_threshold:.2f}",
                limiting_factor="confidence_threshold",
            )

        if request.requested_size < self._config.min_trade_size:
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason=f"Requested size {request.requested_size:.2f} below minimum {self._config.min_trade_size:.2f}",
                limiting_factor="min_trade_size",
            )

        if self._config.max_request_size is not None and request.requested_size > self._config.max_request_size:
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason=f"Requested size {request.requested_size:.2f} exceeds max_request_size {self._config.max_request_size:.2f}",
                limiting_factor="max_request_size",
            )

        # Validate current_state returns are finite (fail-closed on bad adapter data)
        current_net = current_state.get_net_exposure(request.symbol)
        current_total = current_state.get_total_exposure()
        current_same_dir = current_state.get_exposure_by_side(request.side)
        opposite_side: Literal["LONG", "SHORT"] = "SHORT" if request.side == "LONG" else "LONG"
        current_opposite = current_state.get_exposure_by_side(opposite_side)

        for name, value in [
            ("net_exposure", current_net),
            ("total_exposure", current_total),
            ("same_direction_exposure", current_same_dir),
            ("opposite_direction_exposure", current_opposite),
        ]:
            if not math.isfinite(value):
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason=f"Invalid current_state.{name}={value} (not finite)",
                    limiting_factor="current_state",
                )

        net_exposure_after: float
        if request.side == "LONG":
            net_exposure_after = current_net + request.requested_size
        else:
            net_exposure_after = current_net - request.requested_size

        net_exposure_abs = abs(net_exposure_after)

        if net_exposure_abs > self._config.net_exposure_limit:
            return AllocationResult(
                decision=AllocationDecision.REJECTED,
                approved_size=0.0,
                rejected_size=request.requested_size,
                reason=f"Net exposure {net_exposure_abs:.2f} would exceed limit {self._config.net_exposure_limit:.2f}",
                limiting_factor="net_exposure_limit",
            )

        total_exposure_after = current_total + request.requested_size
        if total_exposure_after > self._config.total_exposure_budget:
            clipped_total = self._config.total_exposure_budget - current_total
            if clipped_total < self._config.min_trade_size:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason=f"Total exposure budget exhausted, no room for minimum trade",
                    limiting_factor="total_exposure_budget",
                )
            same_dir_after = current_same_dir + clipped_total
            if same_dir_after > self._config.same_direction_budget:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason=f"Total exposure budget clipping would exceed same-direction budget",
                    limiting_factor="total_exposure_budget",
                )
            if self._config.allow_opposing_offset and current_opposite > 0.0:
                offset = min(clipped_total, current_opposite)
                net_clipped = clipped_total - offset
                return AllocationResult(
                    decision=AllocationDecision.CLIPPED,
                    approved_size=net_clipped,
                    rejected_size=request.requested_size - net_clipped,
                    reason=f"Total exposure budget exceeded, clipped from {request.requested_size:.2f} to {net_clipped:.2f} (offset {offset:.2f} against opposing position)",
                    limiting_factor="total_exposure_budget",
                )
            return AllocationResult(
                decision=AllocationDecision.CLIPPED,
                approved_size=clipped_total,
                rejected_size=request.requested_size - clipped_total,
                reason=f"Total exposure budget {self._config.total_exposure_budget:.2f} exceeded, clipped from {request.requested_size:.2f} to {clipped_total:.2f}",
                limiting_factor="total_exposure_budget",
            )

        same_dir_after = current_same_dir + request.requested_size
        if same_dir_after > self._config.same_direction_budget:
            clipped_same_dir = self._config.same_direction_budget - current_same_dir
            if clipped_same_dir < self._config.min_trade_size:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason=f"Same-direction budget exhausted, no room for minimum trade",
                    limiting_factor="same_direction_budget",
                )
            if self._config.allow_opposing_offset and current_opposite > 0.0:
                offset = min(clipped_same_dir, current_opposite)
                net_clipped = clipped_same_dir - offset
                return AllocationResult(
                    decision=AllocationDecision.CLIPPED,
                    approved_size=net_clipped,
                    rejected_size=request.requested_size - net_clipped,
                    reason=f"Same-direction budget exceeded, clipped from {request.requested_size:.2f} to {net_clipped:.2f} (offset {offset:.2f} against opposing position)",
                    limiting_factor="same_direction_budget",
                )
            return AllocationResult(
                decision=AllocationDecision.CLIPPED,
                approved_size=clipped_same_dir,
                rejected_size=request.requested_size - clipped_same_dir,
                reason=f"Same-direction budget {self._config.same_direction_budget:.2f} exceeded, clipped from {request.requested_size:.2f} to {clipped_same_dir:.2f}",
                limiting_factor="same_direction_budget",
            )

        if self._config.allow_opposing_offset and current_opposite > 0.0:
            offset = min(request.requested_size, current_opposite)
            net_approved = request.requested_size - offset
            if net_approved <= 0:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason=f"After offset {offset:.2f}, net approved size {net_approved:.2f} is zero or negative",
                    limiting_factor="opposing_offset",
                )
            if net_approved < self._config.min_trade_size:
                return AllocationResult(
                    decision=AllocationDecision.REJECTED,
                    approved_size=0.0,
                    rejected_size=request.requested_size,
                    reason=f"After offset {offset:.2f}, remaining size {net_approved:.2f} below minimum trade",
                    limiting_factor="opposing_offset",
                )
            if net_approved < request.requested_size:
                return AllocationResult(
                    decision=AllocationDecision.APPROVED,
                    approved_size=net_approved,
                    rejected_size=offset,
                    reason=f"Approved with offset {offset:.2f} against opposing position",
                    limiting_factor=None,
                )

        return AllocationResult(
            decision=AllocationDecision.APPROVED,
            approved_size=request.requested_size,
            rejected_size=0.0,
            reason="Approved as requested",
            limiting_factor=None,
        )
