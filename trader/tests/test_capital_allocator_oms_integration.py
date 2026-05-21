from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trader.api.models.schemas import StrategyAllocationProfileUpdateRequest
from trader.core.application.risk_engine import RiskLevel
from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    StrategyResourceLimits,
    ValidationResult,
)
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.allocation_management import AllocationManagementService
from trader.services.strategy_runner import StrategyRunner
from trader.storage.in_memory import ControlPlaneInMemoryStorage


class _SignalPlugin:
    def __init__(self, signal: Signal):
        self.strategy_id = ""
        self.name = "SignalPlugin"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.signal = signal

    async def initialize(self, config: dict[str, Any]) -> None:
        pass

    async def on_market_data(self, market_data: MarketData) -> Signal | None:
        del market_data
        return self.signal

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        pass

    async def on_cancel(self, order_id: str, reason: str) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        del config
        return ValidationResult.valid()


def _module_for(plugin: _SignalPlugin) -> Mock:
    module = Mock()
    module.create_plugin = Mock(return_value=plugin)
    module.get_plugin = Mock(return_value=plugin)
    module.build_plugin = Mock(return_value=plugin)
    return module


def _profile(
    strategy_id: str,
    *,
    max_notional: float = 10_000.0,
    max_symbol_exposure: float = 10_000.0,
    min_confidence: float = 0.0,
    enabled: bool = True,
    allow_short: bool = True,
) -> StrategyAllocationProfileUpdateRequest:
    return StrategyAllocationProfileUpdateRequest(
        strategy_id=strategy_id,
        max_notional=max_notional,
        max_symbol_exposure=max_symbol_exposure,
        max_portfolio_weight=1.0,
        min_confidence=min_confidence,
        allow_short=allow_short,
        enabled=enabled,
    )


def _market_data(price: str = "1000") -> MarketData:
    return MarketData(
        symbol="BTCUSDT",
        data_type=MarketDataType.TICKER,
        timestamp=datetime.now(timezone.utc),
        price=Decimal(price),
        volume=Decimal("100"),
    )


async def _load_and_start(
    runner: StrategyRunner,
    plugin: _SignalPlugin,
    *,
    strategy_id: str,
    deployment_id: str,
) -> None:
    with patch("importlib.import_module", return_value=_module_for(plugin)):
        await runner.load_strategy(
            strategy_id=strategy_id,
            version="v1",
            module_path=f"strategies.{strategy_id}",
            deployment_id=deployment_id,
            symbols=["BTCUSDT"],
        )
    await runner.start(deployment_id)


def test_allocation_lock_is_portfolio_wide_across_symbols() -> None:
    runner = StrategyRunner()

    assert runner._get_allocation_lock("BTCUSDT") is runner._get_allocation_lock("ETHUSDT")


@pytest.mark.asyncio
async def test_signal_above_max_notional_is_clipped_before_oms() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", max_notional=1_000.0, max_symbol_exposure=5_000.0),
    )
    oms_callback = AsyncMock(return_value={"order_id": "ord-1", "status": "SUBMITTED"})
    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("2"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin, strategy_id="strategy-a", deployment_id="deploy-a")

    signal = await runner.tick("deploy-a", _market_data())

    assert signal is not None
    assert signal.quantity == Decimal("1")
    oms_callback.assert_called_once()
    sent_signal = oms_callback.call_args.args[1]
    assert sent_signal.quantity == Decimal("1")
    traces = allocation_service.list_traces("deploy-a")
    assert traces[-1].allocation_decision == "clipped"
    assert traces[-1].raw_requested_size == 2000.0
    assert traces[-1].allocated_qty == 1.0
    assert traces[-1].final_order_qty == 1.0
    profile = allocation_service.get_profile("deploy-a")
    assert profile is not None
    assert profile.current_notional == 1000.0


@pytest.mark.asyncio
async def test_rejected_signal_does_not_enter_oms_and_writes_trace() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", enabled=False),
    )
    oms_callback = AsyncMock(return_value={"order_id": "ord-1", "status": "SUBMITTED"})
    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("1"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin, strategy_id="strategy-a", deployment_id="deploy-a")

    signal = await runner.tick("deploy-a", _market_data())

    assert signal is None
    oms_callback.assert_not_called()
    traces = allocation_service.list_traces("deploy-a")
    assert traces[-1].allocation_decision == "rejected"
    assert traces[-1].reject_or_clip_reason == "allocation profile disabled"


@pytest.mark.asyncio
async def test_same_symbol_long_exposure_rejects_second_strategy() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", max_notional=5_000.0, max_symbol_exposure=1_000.0),
    )
    allocation_service.upsert_profile(
        "deploy-b",
        _profile("strategy-b", max_notional=5_000.0, max_symbol_exposure=1_000.0),
    )
    oms_callback = AsyncMock(return_value={"order_id": "ord-1", "status": "SUBMITTED"})
    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin_a = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("0.8"),
            confidence=Decimal("0.9"),
        )
    )
    plugin_b = _SignalPlugin(
        Signal(
            strategy_name="strategy-b",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("0.8"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin_a, strategy_id="strategy-a", deployment_id="deploy-a")
    await _load_and_start(runner, plugin_b, strategy_id="strategy-b", deployment_id="deploy-b")

    first = await runner.tick("deploy-a", _market_data())
    second = await runner.tick("deploy-b", _market_data())

    assert first is not None
    assert second is None
    assert oms_callback.call_count == 1
    profile_a = allocation_service.get_profile("deploy-a")
    assert profile_a is not None
    assert profile_a.current_notional == 800.0
    traces = allocation_service.list_traces("deploy-b")
    assert traces[-1].allocation_decision == "rejected"
    assert traces[-1].reject_or_clip_reason
    assert "Net exposure" in traces[-1].reject_or_clip_reason


@pytest.mark.asyncio
async def test_concurrent_signals_reserve_exposure_before_oms_returns() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", max_notional=5_000.0, max_symbol_exposure=1_000.0),
    )
    allocation_service.upsert_profile(
        "deploy-b",
        _profile("strategy-b", max_notional=5_000.0, max_symbol_exposure=1_000.0),
    )
    first_oms_started = asyncio.Event()
    release_first_oms = asyncio.Event()
    oms_calls: list[str] = []

    async def oms_callback(strategy_id: str, signal: Signal) -> dict[str, str]:
        del signal
        oms_calls.append(strategy_id)
        if strategy_id == "deploy-a":
            first_oms_started.set()
            await release_first_oms.wait()
        return {"order_id": f"ord-{strategy_id}", "status": "SUBMITTED"}

    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin_a = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("0.8"),
            confidence=Decimal("0.9"),
        )
    )
    plugin_b = _SignalPlugin(
        Signal(
            strategy_name="strategy-b",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("0.8"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin_a, strategy_id="strategy-a", deployment_id="deploy-a")
    await _load_and_start(runner, plugin_b, strategy_id="strategy-b", deployment_id="deploy-b")

    first_task = asyncio.create_task(runner.tick("deploy-a", _market_data()))
    await asyncio.wait_for(first_oms_started.wait(), timeout=1)
    pending_profile = allocation_service.get_profile("deploy-a")
    assert pending_profile is not None
    assert pending_profile.current_notional == 0.0
    second = await runner.tick("deploy-b", _market_data())
    release_first_oms.set()
    first = await first_task

    assert first is not None
    assert second is None
    assert oms_calls == ["deploy-a"]
    committed_profile = allocation_service.get_profile("deploy-a")
    assert committed_profile is not None
    assert committed_profile.current_notional == 800.0
    traces = allocation_service.list_traces("deploy-b")
    assert traces[-1].allocation_decision == "rejected"
    assert traces[-1].reject_or_clip_reason
    assert "Net exposure" in traces[-1].reject_or_clip_reason


@pytest.mark.asyncio
async def test_oms_falsy_result_releases_reservation_without_committing_notional() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", max_notional=5_000.0, max_symbol_exposure=1_000.0),
    )
    oms_callback = AsyncMock(
        side_effect=[
            None,
            {"order_id": "ord-2", "status": "SUBMITTED"},
        ]
    )
    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("0.8"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin, strategy_id="strategy-a", deployment_id="deploy-a")

    first = await runner.tick("deploy-a", _market_data())
    failed_profile = allocation_service.get_profile("deploy-a")
    assert failed_profile is not None
    assert failed_profile.current_notional == 0.0

    second = await runner.tick("deploy-a", _market_data())

    assert first is not None
    assert second is not None
    assert oms_callback.call_count == 2
    committed_profile = allocation_service.get_profile("deploy-a")
    assert committed_profile is not None
    assert committed_profile.current_notional == 800.0


@pytest.mark.asyncio
async def test_oms_falsy_result_does_not_reduce_existing_committed_notional() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", max_notional=5_000.0, max_symbol_exposure=2_000.0),
    )
    oms_callback = AsyncMock(
        side_effect=[
            {"order_id": "ord-1", "status": "SUBMITTED"},
            None,
        ]
    )
    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("0.5"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin, strategy_id="strategy-a", deployment_id="deploy-a")

    assert await runner.tick("deploy-a", _market_data()) is not None
    committed_profile = allocation_service.get_profile("deploy-a")
    assert committed_profile is not None
    assert committed_profile.current_notional == 500.0

    assert await runner.tick("deploy-a", _market_data()) is not None

    unchanged_profile = allocation_service.get_profile("deploy-a")
    assert unchanged_profile is not None
    assert unchanged_profile.current_notional == 500.0


@pytest.mark.asyncio
async def test_allocation_profile_hot_update_is_used_by_next_signal() -> None:
    storage = ControlPlaneInMemoryStorage()
    allocation_service = AllocationManagementService(storage)
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", enabled=False),
    )
    oms_callback = AsyncMock(return_value={"order_id": "ord-1", "status": "SUBMITTED"})
    runner = StrategyRunner(
        oms_callback=oms_callback,
        allocation_management=allocation_service,
    )
    plugin = _SignalPlugin(
        Signal(
            strategy_name="strategy-a",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("1000"),
            quantity=Decimal("1"),
            confidence=Decimal("0.9"),
        )
    )
    await _load_and_start(runner, plugin, strategy_id="strategy-a", deployment_id="deploy-a")

    assert await runner.tick("deploy-a", _market_data()) is None
    allocation_service.upsert_profile(
        "deploy-a",
        _profile("strategy-a", enabled=True),
    )
    assert await runner.tick("deploy-a", _market_data()) is not None

    oms_callback.assert_called_once()
    decisions = [trace.allocation_decision for trace in allocation_service.list_traces("deploy-a")]
    assert decisions[-2:] == ["rejected", "approved"]
