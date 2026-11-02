"""A REST collection that changes during pagination is not a complete snapshot."""

import asyncio
from copy import deepcopy

import pytest

from trader.adapters.broker.spot_execution_adapter import BinanceSpotSnapshotProvider
from trader.tests.test_spot_execution_adapter import FakeBroker


class TrackingBroker(FakeBroker):
    """Records how many symbol fetches are in flight at the same time."""

    def __init__(self):
        super().__init__()
        self.inflight = 0
        self.max_inflight = 0

    async def _tracked(self, coroutine):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(0.01)
            return await coroutine
        finally:
            self.inflight -= 1

    async def get_all_orders(self, symbol, start_time_ms=None, limit=1000):
        return await self._tracked(super().get_all_orders(symbol, start_time_ms, limit))

    async def get_my_trades(self, symbol, start_time_ms=None, from_id=None, limit=1000):
        return await self._tracked(super().get_my_trades(symbol, start_time_ms, from_id, limit))


@pytest.mark.asyncio
async def test_snapshot_fetches_symbols_concurrently():
    broker = TrackingBroker()
    provider = BinanceSpotSnapshotProvider(
        broker,
        account_id="acct",
        symbols=["BTCUSDT", "ETHUSDT", "USDCUSDT"],
        clock=lambda: 1_000_100,
    )

    snapshot = await provider.build()

    assert snapshot.complete
    assert broker.max_inflight > 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["balance", "orders"])
async def test_changing_collection_cannot_enable_trading(change):
    broker = FakeBroker()
    original = broker.get_my_trades

    async def moving_trades(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == "balance":
            broker.account = deepcopy(broker.account)
            broker.account["balances"][0]["free"] = "899"
        else:
            broker.orders = []
        return result

    broker.get_my_trades = moving_trades
    provider = BinanceSpotSnapshotProvider(
        broker, account_id="acct", symbols=["BTCUSDT"], clock=lambda: 1_000_100
    )
    assert not (await provider.build()).complete
