from __future__ import annotations

import pytest

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


def test_absolute_profile_sets_effective_budget_for_legacy_payload() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())

    created = service.upsert_profile("deploy-a", _profile_request())

    assert created.allocation_mode == "ABSOLUTE_NOTIONAL"
    assert created.configured_notional == 1_000.0
    assert created.effective_max_notional == 1_000.0
    assert created.max_notional == 1_000.0
    assert created.basis_nav is None


def test_percent_profile_uses_manual_nav_and_hard_cap() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())

    created = service.upsert_profile(
        "deploy-a",
        StrategyAllocationProfileUpdateRequest(
            strategy_id="strategy-a",
            allocation_mode="PERCENT_OF_NAV",
            target_weight=0.25,
            nav_source="manual",
            manual_nav=10_000.0,
            hard_cap_notional=2_000.0,
            max_symbol_exposure=500.0,
            max_portfolio_weight=0.25,
            min_confidence=0.6,
            allow_short=True,
            enabled=True,
        ),
    )

    assert created.basis_nav == 10_000.0
    assert created.configured_notional == 2_500.0
    assert created.effective_max_notional == 2_000.0
    assert created.max_notional == 2_000.0
    assert created.remaining_notional == 2_000.0


def test_percent_profile_without_nav_fails_closed() -> None:
    service = AllocationManagementService(ControlPlaneInMemoryStorage())

    try:
        service.upsert_profile(
            "deploy-a",
            StrategyAllocationProfileUpdateRequest(
                strategy_id="strategy-a",
                allocation_mode="PERCENT_OF_NAV",
                target_weight=0.2,
                nav_source="paper_nav",
                max_symbol_exposure=500.0,
                max_portfolio_weight=0.2,
            ),
        )
    except ValueError as exc:
        assert "basis NAV" in str(exc)
    else:
        raise AssertionError("percent allocation without NAV should fail closed")


@pytest.mark.asyncio
async def test_percent_profile_paper_nav_uses_pg_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = ControlPlaneInMemoryStorage()
    service = AllocationManagementService(storage)

    async def fake_get_nav_series_pg(
        deployment_id: str, since_ms: int | None = None, limit: int = 500
    ) -> list[dict]:
        assert deployment_id == "deploy-a"
        assert since_ms is None
        assert limit == 1
        return [{"deployment_id": deployment_id, "equity": 12_000.0, "timestamp_ms": 1000}]

    monkeypatch.setattr(
        "trader.storage.nav_store.get_nav_series_pg",
        fake_get_nav_series_pg,
    )

    created = await service.upsert_profile_async(
        "deploy-a",
        StrategyAllocationProfileUpdateRequest(
            strategy_id="strategy-a",
            allocation_mode="PERCENT_OF_NAV",
            target_weight=0.25,
            nav_source="paper_nav",
            max_symbol_exposure=500.0,
            max_portfolio_weight=0.25,
        ),
    )

    assert created.basis_nav == 12_000.0
    assert created.configured_notional == 3_000.0
    assert created.effective_max_notional == 3_000.0


def test_profile_update_appends_audit_event() -> None:
    storage = ControlPlaneInMemoryStorage()
    service = AllocationManagementService(storage)

    service.upsert_profile("deploy-a", _profile_request())
    service.upsert_profile(
        "deploy-a",
        StrategyAllocationProfileUpdateRequest(
            strategy_id="strategy-a",
            allocation_mode="PERCENT_OF_NAV",
            target_weight=0.1,
            nav_source="manual",
            manual_nav=20_000.0,
            max_symbol_exposure=500.0,
            max_portfolio_weight=0.1,
        ),
    )

    events = storage.list_events(stream_key="allocation:profiles")
    assert [event["event_type"] for event in events] == [
        "allocation.profile_updated",
        "allocation.profile_updated",
    ]
    payload = events[-1]["payload"]
    assert payload["deployment_id"] == "deploy-a"
    assert payload["old_profile"]["allocation_mode"] == "ABSOLUTE_NOTIONAL"
    assert payload["new_profile"]["allocation_mode"] == "PERCENT_OF_NAV"
    assert payload["new_profile"]["effective_max_notional"] == 2_000.0


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
