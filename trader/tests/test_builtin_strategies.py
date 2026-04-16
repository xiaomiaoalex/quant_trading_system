"""Tests for built-in strategy plugins under trader.strategies."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    validate_strategy_plugin,
)
from trader.core.domain.models.signal import SignalType
from trader.services.strategy_runner import StrategyRunner, StrategyStatus


def _market_data(price: str, ts: datetime) -> MarketData:
    return MarketData(
        symbol="BTCUSDT",
        data_type=MarketDataType.TICKER,
        price=Decimal(price),
        volume=Decimal("1"),
        timestamp=ts,
    )


@pytest.mark.parametrize(
    "module_path",
    [
        "trader.strategies.ema_cross_btc",
        "trader.strategies.rsi_grid",
        "trader.strategies.dca_btc",
    ],
)
def test_builtin_strategy_modules_expose_valid_plugin(module_path: str) -> None:
    module = importlib.import_module(module_path)
    assert hasattr(module, "get_plugin")

    plugin = module.get_plugin()
    is_valid, error = validate_strategy_plugin(plugin)
    assert is_valid, error
    assert plugin.validate().is_valid


async def test_ema_cross_btc_emits_buy_on_upward_crossover() -> None:
    plugin = importlib.import_module("trader.strategies.ema_cross_btc").get_plugin()
    await plugin.initialize(
        {
            "fast_period": 3,
            "slow_period": 5,
            "order_size": "0.01",
            "min_confidence": "0.60",
        }
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = ["100", "98", "96", "94", "92", "94", "96", "99", "102"]
    signals = []
    for idx, price in enumerate(prices):
        signal = await plugin.on_market_data(_market_data(price, start + timedelta(seconds=idx)))
        signals.append(signal)

    assert any(sig is not None and sig.signal_type == SignalType.BUY for sig in signals)


async def test_rsi_grid_respects_grid_filter_and_emits_reversal_signals() -> None:
    plugin = importlib.import_module("trader.strategies.rsi_grid").get_plugin()
    await plugin.initialize(
        {
            "rsi_period": 3,
            "oversold": "35",
            "overbought": "65",
            "grid_step_pct": "0.02",
            "order_size": "0.01",
        }
    )

    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    warmup = ["100", "95", "90", "85"]
    first_buy = None
    for idx, price in enumerate(warmup):
        signal = await plugin.on_market_data(_market_data(price, start + timedelta(seconds=idx)))
        if signal is not None and first_buy is None:
            first_buy = signal

    blocked_by_grid = await plugin.on_market_data(_market_data("84", start + timedelta(seconds=10)))
    assert first_buy is not None
    assert first_buy.signal_type == SignalType.BUY
    assert blocked_by_grid is None

    sell_signal = None
    recovery = ["95", "105", "115"]
    for idx, price in enumerate(recovery, start=20):
        signal = await plugin.on_market_data(_market_data(price, start + timedelta(seconds=idx)))
        if signal is not None and signal.signal_type == SignalType.SELL:
            sell_signal = signal
            break

    assert sell_signal is not None


async def test_dca_btc_supports_regular_and_dip_buy() -> None:
    plugin = importlib.import_module("trader.strategies.dca_btc").get_plugin()
    await plugin.initialize(
        {
            "interval_seconds": 60,
            "min_buy_gap_seconds": 10,
            "base_order_size": "0.001",
            "dip_threshold_pct": "0.02",
            "dip_multiplier": "2",
        }
    )

    start = datetime(2026, 1, 3, tzinfo=timezone.utc)
    first = await plugin.on_market_data(_market_data("100", start))
    no_trade = await plugin.on_market_data(_market_data("99", start + timedelta(seconds=30)))
    dip_buy = await plugin.on_market_data(_market_data("96", start + timedelta(seconds=45)))
    regular_buy = await plugin.on_market_data(_market_data("97", start + timedelta(seconds=120)))

    assert first is not None and first.signal_type == SignalType.BUY
    assert first.quantity == Decimal("0.001")
    assert no_trade is None
    assert dip_buy is not None and dip_buy.quantity == Decimal("0.002")
    assert regular_buy is not None and regular_buy.quantity == Decimal("0.001")


async def test_dca_btc_rejects_invalid_update_config() -> None:
    plugin = importlib.import_module("trader.strategies.dca_btc").get_plugin()
    await plugin.initialize({"interval_seconds": 60})
    result = await plugin.update_config({"interval_seconds": 0})

    assert not result.is_valid
    assert plugin.interval_seconds == 60


async def test_strategy_runner_loads_all_builtin_strategies() -> None:
    runner = StrategyRunner()
    builtins = [
        ("ema_cross_btc", "trader.strategies.ema_cross_btc"),
        ("rsi_grid", "trader.strategies.rsi_grid"),
        ("dca_btc", "trader.strategies.dca_btc"),
    ]
    for strategy_id, module_path in builtins:
        info = await runner.load_strategy(
            strategy_id=strategy_id,
            version="v1",
            module_path=module_path,
            config={},
        )
        assert info.status == StrategyStatus.LOADED
        await runner.unload_strategy(strategy_id)
