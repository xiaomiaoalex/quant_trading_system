from __future__ import annotations

from trader.api.models.schemas import (
    AllocationTraceCreateRequest,
    StrategyAllocationProfileUpdateRequest,
)
from trader.services.allocation_management import AllocationManagementService
from trader.storage.in_memory import ControlPlaneInMemoryStorage


def _profile_request(strategy_id: str = "strategy-a") -> StrategyAllocationProfileUpdateRequest:
    return StrategyAllocationProfileUpdateRequest(
        strategy_id=strategy_id,
        max_notional=1_000.0,
        max_symbol_exposure=500.0,
        max_portfolio_weight=1.0,
        min_confidence=0.6,
        allow_short=True,
        enabled=True,
    )


def test_get_profile_returns_none_for_missing_deployment() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())

    assert service.get_profile("missing") is None


def test_upsert_and_get_profile_round_trip() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())

    created = service.upsert_profile("deploy-a", _profile_request())
    fetched = service.get_profile("deploy-a")

    assert fetched is not None
    assert fetched.deployment_id == "deploy-a"
    assert fetched.strategy_id == "strategy-a"
    assert fetched.max_notional == created.max_notional
    assert fetched.remaining_notional == 1_000.0


def test_add_runtime_notional_updates_current_and_remaining_notional() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())
    service.upsert_profile("deploy-a", _profile_request())

    updated = service.add_runtime_notional("deploy-a", 250.0)

    assert updated is not None
    assert updated.current_notional == 250.0
    assert updated.remaining_notional == 750.0


def test_add_runtime_notional_clamps_release_at_zero() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())
    service.upsert_profile("deploy-a", _profile_request())
    service.add_runtime_notional("deploy-a", 100.0)

    updated = service.add_runtime_notional("deploy-a", -250.0)

    assert updated is not None
    assert updated.current_notional == 0.0
    assert updated.remaining_notional == 1_000.0


def test_add_runtime_notional_missing_profile_is_noop() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())

    assert service.add_runtime_notional("missing", 100.0) is None


def test_append_trace_and_append_trace_data_share_storage_contract() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())
    service.upsert_profile("deploy-a", _profile_request())

    created = service.append_trace(
        "deploy-a",
        AllocationTraceCreateRequest(
            strategy_id="strategy-a",
            symbol="BTCUSDT",
            raw_requested_size=1_000.0,
            risk_sized_qty=1.0,
            allocated_qty=0.5,
            final_order_qty=0.5,
            allocation_decision="clipped",
            reject_or_clip_reason="budget",
        ),
    )
    created_from_data = service.append_trace_data(
        "deploy-a",
        {
            "strategy_id": "strategy-a",
            "symbol": "ETHUSDT",
            "raw_requested_size": 500.0,
            "risk_sized_qty": 1.0,
            "allocated_qty": 1.0,
            "final_order_qty": 1.0,
            "allocation_decision": "approved",
            "reject_or_clip_reason": None,
        },
    )

    traces = service.list_traces("deploy-a")
    assert [trace.trace_id for trace in traces] == [
        created.trace_id,
        created_from_data.trace_id,
    ]
    assert traces[0].deployment_id == "deploy-a"
    assert traces[1].symbol == "ETHUSDT"
