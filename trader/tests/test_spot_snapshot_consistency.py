"""A REST collection that changes during pagination is not a complete snapshot."""

from copy import deepcopy

import pytest

from trader.adapters.broker.spot_execution_adapter import BinanceSpotSnapshotProvider
from trader.tests.test_spot_execution_adapter import FakeBroker


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
