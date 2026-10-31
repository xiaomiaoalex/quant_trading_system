"""Recovery must page orders as well as trades, without guessing completeness."""

import pytest

from trader.adapters.broker.spot_execution_adapter import BinanceSpotSnapshotProvider
from trader.tests.test_spot_execution_adapter import FakeBroker, broker_order


@pytest.mark.asyncio
async def test_recovery_pages_order_mapping_before_matching_later_trade():
    class Broker(FakeBroker):
        async def get_all_orders(self, symbol, start_time_ms=None, limit=1000, from_order_id=None):
            return (
                [broker_order(order_id="1")]
                if from_order_id is None
                else [broker_order(order_id="2", cl_ord_id="qts-2")]
            )

        async def get_my_trades(self, symbol, start_time_ms=None, from_id=None, limit=1000):
            return []

    provider = BinanceSpotSnapshotProvider(
        Broker(),
        account_id="acct",
        symbols=["BTCUSDT"],
        clock=lambda: 1_000_100,
        page_limit=1,
        max_pages=3,
    )
    orders, complete = await provider._orders("BTCUSDT", 1_000_100)
    assert [item.broker_order_id for item in orders] == ["1", "2"]
    assert not complete  # repeated/non-progress page is not proof of completion


@pytest.mark.asyncio
async def test_recovery_order_pagination_finishes_only_after_short_page():
    class Broker(FakeBroker):
        async def get_all_orders(self, symbol, start_time_ms=None, limit=1000, from_order_id=None):
            return (
                [broker_order(order_id="1"), broker_order(order_id="2")]
                if from_order_id is None
                else [broker_order(order_id="3")]
            )

    provider = BinanceSpotSnapshotProvider(
        Broker(), account_id="acct", symbols=[], clock=lambda: 1_000_100, page_limit=2
    )
    orders, complete = await provider._orders("BTCUSDT", 1_000_100)
    assert complete and [o.broker_order_id for o in orders] == ["1", "2", "3"]
