"""Latency guard: trading events must not degrade materially under a large replay."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from decimal import Decimal

import pytest

from trader.tests.test_trading_service import Clock, FakeExchange, bootstrap, command, make_service
from trader.trading.models import Execution

REPLAY_SCRIPT = r"""
from decimal import Decimal
from trader.trading.backtest import EventBacktestRuntime

count = 100_000
bars = []
for index in range(count):
    opened = index * 60_000
    bars.append({
        "open_time_ms": opened,
        "close_time_ms": opened + 60_000,
        "available_at_ms": opened + 60_000,
        "open": "100", "high": "101", "low": "99", "close": "100",
        "volume": "1",
    })
dataset = {
    "dataset_id": "ds_perf",
    "identity": {
        "venue": "binance", "market_type": "SPOT", "symbol": "BTCUSDT",
        "base_asset": "BTC", "quote_asset": "USDT", "interval": "1m",
        "source": "fixture", "version": "1", "schema_version": 1,
        "interval_ms": 60_000,
    },
    "bars": bars,
    "quality": {"complete": True, "bar_count": count},
}
runtime = EventBacktestRuntime(
    datasets=[dataset],
    strategy=lambda context, bar: [],
    initial_cash=Decimal("1000"),
    fee_bps=Decimal("10"),
    slippage_bps=Decimal("0"),
)
result = runtime.run()
print(len(result.equity_curve))
"""


def percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


async def measure(service, execution, samples=400):
    latencies = []
    for _ in range(samples):
        started = time.perf_counter()
        await service.record_execution(execution)
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


@pytest.mark.asyncio
async def test_trading_event_p99_under_large_replay_stays_within_tolerance():
    service = make_service(FakeExchange(), clock=Clock())
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("1"), price=Decimal("100")))
    execution = Execution(
        "acct",
        receipt.cl_ord_id,
        "BTCUSDT",
        "e-1",
        Decimal("0.1"),
        Decimal("100"),
        Decimal("0.01"),
        "USDT",
        1_000_000,
    )
    await service.record_execution(execution)

    await measure(service, execution, samples=50)  # warm caches
    idle = await measure(service, execution)

    # The research worker runs in a separate process, so the replay cannot hold
    # the trading runtime's GIL.
    process = subprocess.Popen(
        [sys.executable, "-c", REPLAY_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
    )
    await asyncio.sleep(0.5)
    loaded = await measure(service, execution)
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr
    assert stdout.strip().splitlines()[-1] == "100000"

    idle_p99 = percentile(idle, 0.99)
    loaded_p99 = percentile(loaded, 0.99)
    tolerance_ms = max(idle_p99 * 0.20, 10.0)
    assert loaded_p99 - idle_p99 <= tolerance_ms, (
        f"idle_p99={idle_p99:.2f}ms loaded_p99={loaded_p99:.2f}ms "
        f"tolerance={tolerance_ms:.2f}ms"
    )
