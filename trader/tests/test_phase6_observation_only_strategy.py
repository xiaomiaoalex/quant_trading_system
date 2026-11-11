"""Tests for the observation-only continuation strategy plugin."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    describe_plugin_warmup,
    validate_strategy_plugin,
)
from trader.strategies.observation_only import ObservationOnlyStrategy, create_plugin, get_plugin
from trader.trading.research import load_plugin


def test_protocol_and_zero_warmup():
    plugin = ObservationOnlyStrategy()
    valid, message = validate_strategy_plugin(plugin)
    assert valid, message
    spec = describe_plugin_warmup(plugin)
    assert spec.stateful is False
    assert spec.events == 0
    assert spec.declared is True
    assert plugin.validate().is_valid


def test_never_emits_a_signal():
    async def run():
        plugin = ObservationOnlyStrategy()
        await plugin.initialize({})
        market = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TICKER,
            price=Decimal("74000"),
            volume=Decimal("0"),
            timestamp=datetime.now(timezone.utc),
        )
        assert await plugin.on_market_data(market) is None
        assert await plugin.on_market_data(market) is None
        await plugin.shutdown()

    asyncio.run(run())


def test_factory_and_loader():
    assert isinstance(create_plugin(), ObservationOnlyStrategy)
    assert isinstance(get_plugin(), ObservationOnlyStrategy)
    assert isinstance(
        load_plugin("trader.strategies.observation_only", {}), ObservationOnlyStrategy
    )


def _market():
    return MarketData(
        symbol="BTCUSDT",
        data_type=MarketDataType.TICKER,
        price=Decimal("74000"),
        volume=Decimal("0"),
        timestamp=datetime.now(timezone.utc),
    )


def test_fire_test_observation_only_never_emits():
    async def run():
        plugin = load_plugin("trader.strategies.fire_test", {"observation_only": True})
        await plugin.initialize({"observation_only": True})
        for _ in range(3):
            assert await plugin.on_market_data(_market()) is None

    asyncio.run(run())


def test_fire_test_default_still_emits():
    async def run():
        plugin = load_plugin("trader.strategies.fire_test", {})
        await plugin.initialize({})
        assert await plugin.on_market_data(_market()) is not None

    asyncio.run(run())
