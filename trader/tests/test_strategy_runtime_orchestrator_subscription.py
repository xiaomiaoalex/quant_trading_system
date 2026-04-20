"""
Strategy Runtime Orchestrator Subscription Tests
================================================
"""
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock, AsyncMock

from trader.services.strategy_runtime_orchestrator import StrategyRuntimeOrchestrator


class _FakePublicStream:
    def __init__(self):
        self._public_config = SimpleNamespace(streams=["btcusdt@trade"])
        self.stop = AsyncMock(return_value=None)
        self.start = AsyncMock(return_value=None)
        self._running = True

    def is_running(self) -> bool:
        return self._running


class _FakeConnector:
    def __init__(self):
        self.public_stream = _FakePublicStream()
        self._handlers = []

    def register_market_handler(self, handler):
        self._handlers.append(handler)


@pytest.mark.asyncio
async def test_start_strategy_updates_public_stream_subscriptions():
    runner = MagicMock()
    runner.get_status = MagicMock(return_value=SimpleNamespace(strategy_id="s1"))
    connector = _FakeConnector()

    orchestrator = StrategyRuntimeOrchestrator(runner=runner, connector=connector)
    await orchestrator.start_strategy("s1", "ETHUSDT")

    streams = connector.public_stream._public_config.streams
    assert "ethusdt@trade" in streams
    assert "ethusdt@kline_1m" in streams
    connector.public_stream.stop.assert_awaited_once()
    connector.public_stream.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_strategy_same_symbol_does_not_duplicate_subscriptions():
    runner = MagicMock()
    runner.get_status = MagicMock(return_value=SimpleNamespace(strategy_id="s1"))
    connector = _FakeConnector()

    orchestrator = StrategyRuntimeOrchestrator(runner=runner, connector=connector)
    await orchestrator.start_strategy("s1", "ETHUSDT")

    runner.get_status = MagicMock(return_value=SimpleNamespace(strategy_id="s2"))
    await orchestrator.start_strategy("s2", "ETHUSDT")

    streams = connector.public_stream._public_config.streams
    assert streams.count("ethusdt@trade") == 1
    assert streams.count("ethusdt@kline_1m") == 1
