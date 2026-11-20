"""The market loop must distinguish readiness fences from real input faults."""

from __future__ import annotations

import asyncio

import pytest

from trader.runtime.trading_worker_main import _market_loop
from trader.trading.models import NotReady


class _Warmup:
    value = "WARM"


class _Runner:
    warm = True
    warmup_state = _Warmup()


class _Broker:
    async def get_klines(self, symbol, interval, limit):
        # Open 0, close time 59999, close price 100: a closed 1m BAR_V1 row.
        return [[0, "1", "1", "1", "100", "1", 59999, 0, 0, 0, 0, "0"]]


class _Runtime:
    strategy_start_allowed = True
    strategy_runner = _Runner()

    def __init__(self):
        self.clock = lambda: 100_000
        self.blocks: list[str] = []

    async def on_market_data(self, data, event_id=None):
        raise NotReady("TRADING_RUNTIME_NOT_READY")

    async def block(self, reason):
        self.blocks.append(reason)


class _FailingRuntime(_Runtime):
    async def on_market_data(self, data, event_id=None):
        raise RuntimeError("strategy exploded")


async def _run_one_turn(runtime) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            _market_loop(
                runtime,
                _Broker(),
                {"BTCUSDT": ("BTC", "USDT")},
                interval="1m",
                poll_seconds=0.01,
            ),
            timeout=0.2,
        )


@pytest.mark.asyncio
async def test_readiness_fence_is_not_turned_into_an_account_block() -> None:
    runtime = _Runtime()
    await _run_one_turn(runtime)
    assert runtime.blocks == []


@pytest.mark.asyncio
async def test_real_strategy_fault_still_blocks_new_risk() -> None:
    runtime = _FailingRuntime()
    await _run_one_turn(runtime)
    assert runtime.blocks and set(runtime.blocks) == {"STRATEGY_OR_MARKET_DATA_FAILED"}
