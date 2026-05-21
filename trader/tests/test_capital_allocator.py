from __future__ import annotations

import math

import pytest

from trader.services.capital_allocator import (
    AllocationDecision,
    CapitalAllocator,
    CapitalAllocatorConfig,
    SimplePortfolioState,
    StrategyAllocationRequest,
)


def _allocator(
    *,
    total_exposure_budget: float = 10_000.0,
    net_exposure_limit: float = 10_000.0,
    same_direction_budget: float = 10_000.0,
    min_trade_size: float = 10.0,
    confidence_threshold: float = 0.5,
    allow_opposing_offset: bool = True,
) -> CapitalAllocator:
    return CapitalAllocator(
        CapitalAllocatorConfig(
            total_exposure_budget=total_exposure_budget,
            net_exposure_limit=net_exposure_limit,
            same_direction_budget=same_direction_budget,
            min_trade_size=min_trade_size,
            confidence_threshold=confidence_threshold,
            allow_opposing_offset=allow_opposing_offset,
        )
    )


def _request(
    *,
    side: str = "LONG",
    requested_size: float = 500.0,
    confidence: float = 0.9,
) -> StrategyAllocationRequest:
    return StrategyAllocationRequest(
        strategy_id="strategy-a",
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
        requested_size=requested_size,
        signal_confidence=confidence,
    )


def test_approve_when_request_is_inside_all_budgets() -> None:
    result = _allocator().allocate(_request(), SimplePortfolioState())

    assert result.decision == AllocationDecision.APPROVED
    assert result.approved_size == 500.0
    assert result.rejected_size == 0.0


def test_rejects_confidence_below_threshold() -> None:
    result = _allocator(confidence_threshold=0.8).allocate(
        _request(confidence=0.79),
        SimplePortfolioState(),
    )

    assert result.decision == AllocationDecision.REJECTED
    assert result.limiting_factor == "confidence_threshold"


def test_rejects_request_below_min_trade_size() -> None:
    result = _allocator(min_trade_size=100.0).allocate(
        _request(requested_size=99.0),
        SimplePortfolioState(),
    )

    assert result.decision == AllocationDecision.REJECTED
    assert result.limiting_factor == "min_trade_size"


def test_rejects_invalid_nan_or_inf_request_inputs() -> None:
    request_nan = _request(requested_size=math.nan)
    request_inf_confidence = _request(confidence=math.inf)

    result_nan = _allocator().allocate(request_nan, SimplePortfolioState())
    result_inf = _allocator().allocate(request_inf_confidence, SimplePortfolioState())

    assert result_nan.decision == AllocationDecision.REJECTED
    assert result_nan.limiting_factor == "requested_size"
    assert result_inf.decision == AllocationDecision.REJECTED
    assert result_inf.limiting_factor == "signal_confidence"


def test_rejects_non_finite_portfolio_state() -> None:
    result = _allocator().allocate(
        _request(),
        SimplePortfolioState(total_exposure=math.inf),
    )

    assert result.decision == AllocationDecision.REJECTED
    assert result.limiting_factor == "current_state"


def test_rejects_when_net_exposure_would_exceed_limit() -> None:
    result = _allocator(net_exposure_limit=1_000.0).allocate(
        _request(requested_size=400.0),
        SimplePortfolioState(
            positions={"BTCUSDT": {"LONG": 700.0, "SHORT": 0.0}},
        ),
    )

    assert result.decision == AllocationDecision.REJECTED
    assert result.limiting_factor == "net_exposure_limit"


def test_clips_when_total_exposure_budget_has_partial_room() -> None:
    result = _allocator(total_exposure_budget=1_000.0, min_trade_size=10.0).allocate(
        _request(requested_size=300.0),
        SimplePortfolioState(total_exposure=900.0),
    )

    assert result.decision == AllocationDecision.CLIPPED
    assert result.approved_size == 100.0
    assert result.rejected_size == 200.0
    assert result.limiting_factor == "total_exposure_budget"


def test_rejects_when_total_exposure_remaining_room_is_below_minimum() -> None:
    result = _allocator(total_exposure_budget=1_000.0, min_trade_size=100.0).allocate(
        _request(requested_size=300.0),
        SimplePortfolioState(total_exposure=950.0),
    )

    assert result.decision == AllocationDecision.REJECTED
    assert result.limiting_factor == "total_exposure_budget"


def test_clips_when_same_direction_budget_has_partial_room() -> None:
    result = _allocator(same_direction_budget=1_000.0, min_trade_size=10.0).allocate(
        _request(requested_size=300.0),
        SimplePortfolioState(long_exposure=900.0),
    )

    assert result.decision == AllocationDecision.CLIPPED
    assert result.approved_size == 100.0
    assert result.rejected_size == 200.0
    assert result.limiting_factor == "same_direction_budget"


def test_rejects_when_same_direction_remaining_room_is_below_minimum() -> None:
    result = _allocator(same_direction_budget=1_000.0, min_trade_size=100.0).allocate(
        _request(requested_size=300.0),
        SimplePortfolioState(long_exposure=950.0),
    )

    assert result.decision == AllocationDecision.REJECTED
    assert result.limiting_factor == "same_direction_budget"


def test_applies_opposing_offset_to_approved_size() -> None:
    result = _allocator(allow_opposing_offset=True).allocate(
        _request(side="LONG", requested_size=500.0),
        SimplePortfolioState(
            short_exposure=200.0,
            positions={"BTCUSDT": {"LONG": 0.0, "SHORT": 200.0}},
        ),
    )

    assert result.decision == AllocationDecision.APPROVED
    assert result.approved_size == 300.0
    assert result.rejected_size == 200.0
    assert "offset 200.00" in result.reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_exposure_budget": -1.0},
        {"net_exposure_limit": -1.0},
        {"same_direction_budget": -1.0},
        {"min_trade_size": -1.0},
        {"confidence_threshold": 1.1},
    ],
)
def test_config_rejects_invalid_thresholds(kwargs: dict[str, float]) -> None:
    config_kwargs = {
        "total_exposure_budget": 1_000.0,
        "net_exposure_limit": 1_000.0,
        "same_direction_budget": 1_000.0,
        **kwargs,
    }
    with pytest.raises(ValueError):
        CapitalAllocatorConfig(**config_kwargs)
